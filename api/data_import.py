"""表格导入：磁盘暂存、只读预校验、显式确认后的 MySQL 事务追加。"""
from __future__ import annotations

import asyncio
import csv
import shutil
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from typing import Literal
from api import business_duplicates as duplicates

import mysql.connector
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from tools.mysql_tools import _get_connection
from api import import_connections as connections
from utils.path_utils import PROJECT_ROOT
from utils.logger_utils import get_logger

router = APIRouter(prefix='/api/data-import', tags=['data-import'])
logger = get_logger('tanyuan.import')
_lock = threading.RLock()
_initialized: set[str] = set()
_MAX_BYTES = 20 * 1024 * 1024
_MAX_ROWS = 5000
_IDENT = re.compile(r'[A-Za-z0-9_]{1,64}\Z')


def _root() -> Path:
    root = Path(os.getenv('IMPORT_STORAGE_DIR', str(PROJECT_ROOT / 'data' / 'imports'))).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextmanager
def _db():
    path = str(_root() / 'imports.sqlite3')
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    if path not in _initialized:
        connection.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, payload TEXT NOT NULL, fingerprint TEXT, status TEXT NOT NULL)')
        # 进程中断时无法从本地状态确定 MySQL 是否已提交，禁止自动重试。
        for row in connection.execute("SELECT id,payload FROM batches WHERE status='committing'").fetchall():
            payload = json.loads(row['payload'])
            payload.update(status='uncertain', message='上次写入被中断，结果待核实。请核对数据库，勿重复导入。')
            connection.execute('UPDATE batches SET status=?,payload=? WHERE id=?',
                               ('uncertain', json.dumps(payload, ensure_ascii=False), row['id']))
        connection.commit()
        _initialized.add(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _save(batch):
    with _db() as connection:
        connection.execute('INSERT OR REPLACE INTO batches(id,payload,fingerprint,status) VALUES(?,?,?,?)',
                           (batch['id'], json.dumps(batch, ensure_ascii=False), batch.get('fingerprint'), batch['status']))


def _load(batch_id):
    if not re.fullmatch(r'[a-f0-9]{32}', batch_id):
        raise HTTPException(400, '非法导入批次')
    with _db() as connection:
        row = connection.execute('SELECT payload FROM batches WHERE id=?', (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, '导入批次不存在')
    return json.loads(row['payload'])


def _summary(batch):
    return {key: batch.get(key) for key in ('id', 'filename', 'status', 'created_at', 'finished_at', 'table', 'sheet', 'row_count', 'inserted_rows', 'skipped_rows', 'duplicate_policy', 'dedupe_summary', 'message', 'connection_id', 'destination')}


def _schema():
    profile=connections.current()
    connection = connections.connect() if profile and profile['id']!='default' else _get_connection()
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'")
        tables = {r['TABLE_NAME']: {'name': r['TABLE_NAME'], 'engine': r['ENGINE'], 'columns': []}
                  for r in cursor.fetchall() if _IDENT.fullmatch(r['TABLE_NAME'])}
        cursor.execute("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA,COLUMN_COMMENT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION")
        for row in cursor.fetchall():
            if row['TABLE_NAME'] not in tables or not _IDENT.fullmatch(row['COLUMN_NAME']):
                continue
            extra = (row['EXTRA'] or '').lower()
            generated = 'auto_increment' in extra or 'virtual generated' in extra or 'stored generated' in extra
            column = {'name': row['COLUMN_NAME'], 'type': row['COLUMN_TYPE'],
                      'nullable': row['IS_NULLABLE'] == 'YES', 'default': str(row['COLUMN_DEFAULT']) if row['COLUMN_DEFAULT'] is not None else None,
                      'generated': generated, 'comment': row['COLUMN_COMMENT'] or ''}
            column['required'] = not generated and not column['nullable'] and column['default'] is None
            tables[row['TABLE_NAME']]['columns'].append(column)
        for target in tables.values():duplicates.enrich_target(connection,target)
        from casework.mirror import managed
        return {name:table for name,table in tables.items() if not managed(name)}
    finally:
        connection.close()


def _target(table):
    from casework.mirror import reject_direct_write
    reject_direct_write(table)
    if not _IDENT.fullmatch(table):
        raise HTTPException(400, '非法目标表名')
    schema = _schema()
    if table not in schema:
        raise HTTPException(404, '目标表不存在或不可访问')
    target = schema[table]
    if target['engine'] != 'InnoDB':
        raise HTTPException(400, '仅支持使用 InnoDB 事务引擎的表')
    return target


def _schema_hash(target):
    return hashlib.sha256(json.dumps(target, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _destination():
    return connections.destination()


def _writer_configured():
    return connections.configured()


def _writer():
    profile=connections.current()
    if profile and profile['id']!='default':
        if not _writer_configured():raise HTTPException(409,'所选连接未配置独立导入账号，可先预览校验')
        return connections.connect(write=True)
    if not _writer_configured():
        raise HTTPException(409, '尚未配置独立导入账号 MYSQL_IMPORT_USER / MYSQL_IMPORT_PASSWORD；可先上传和预览校验')
    return mysql.connector.connect(
        host=os.getenv('MYSQL_HOST', 'localhost'), port=int(os.getenv('MYSQL_PORT', '3306')),
        user=os.environ['MYSQL_IMPORT_USER'], password=os.environ['MYSQL_IMPORT_PASSWORD'],
        database=os.getenv('MYSQL_DATABASE', 'department_workbench'), charset=os.getenv('MYSQL_CHARSET', 'utf8mb4'),
        autocommit=False, use_pure=True, connection_timeout=15, read_timeout=30, write_timeout=30,
    )


def _cell(value):
    if value is None: return ''
    if isinstance(value, (date, datetime)): return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, bool): return '1' if value else '0'
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError('表格含无效数值')
        if value.is_integer(): return str(int(value))
    return str(value)


def _sheet(name, records):
    iterator = iter(records)
    try:
        header = next(iterator)
    except StopIteration:
        return None
    header = [_cell(v).strip() for v in header]
    while header and not header[-1]: header.pop()
    if not header: return None
    if len(header) > 100: raise ValueError('每张工作表最多 100 列')
    if any(not v for v in header) or len(header) != len(set(header)):
        raise ValueError(f'工作表“{name}”首行须为非空且不重复的列名')
    rows = []
    for number, values in enumerate(iterator, 2):
        if number > _MAX_ROWS + 1: raise ValueError('每张工作表最多 5000 行数据，请拆分文件')
        cells = [_cell(v) for v in values]
        if not any(v.strip() for v in cells): continue
        if any(v.strip() for v in cells[len(header):]):
            raise ValueError(f'工作表“{name}”第 {number} 行超出表头列数')
        cells = (cells + [''] * len(header))[:len(header)]
        rows.append({'row': number, 'cells': cells})
    return {'name': name, 'headers': header, 'rows': rows} if rows else None


def _parse(filename, raw):
    suffix = Path(filename).suffix.lower()
    sheets = []
    if suffix == '.csv':
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('gb18030')
        result = _sheet('CSV', csv.reader(io.StringIO(text)))
        if result: sheets.append(result)
    elif suffix == '.xlsx':
        import openpyxl
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError('解压后的 Excel 超过 100 MB，请拆分文件')
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
        try:
            if len(workbook.worksheets) > 20: raise ValueError('最多支持 20 张工作表')
            for worksheet in workbook.worksheets:
                if worksheet.max_column and worksheet.max_column > 100: raise ValueError('每张工作表最多 100 列')
                def records():
                    for row in worksheet.iter_rows():
                        if any(cell.data_type in ('f', 'e') for cell in row):
                            raise ValueError(f'工作表“{worksheet.title}”含公式，请粘贴为值后再导入')
                        yield [cell.value for cell in row]
                result = _sheet(worksheet.title, records())
                if result: sheets.append(result)
        finally: workbook.close()
    elif suffix == '.xls':
        import xlrd
        workbook = xlrd.open_workbook(file_contents=raw)
        try:
            if workbook.nsheets > 20: raise ValueError('最多支持 20 张工作表')
            for worksheet in workbook.sheets():
                def records():
                    for row in range(worksheet.nrows):
                        if any(cell.ctype == xlrd.XL_CELL_ERROR for cell in worksheet.row(row)):
                            raise ValueError(f'工作表“{worksheet.name}”含 Excel 错误值，请修正后再导入')
                        yield [xlrd.xldate_as_datetime(cell.value, workbook.datemode) if cell.ctype == xlrd.XL_CELL_DATE else cell.value
                               for cell in worksheet.row(row)]
                result = _sheet(worksheet.name, records())
                if result: sheets.append(result)
        finally: workbook.release_resources()
    else: raise ValueError('仅支持 .xlsx、.xls、.csv 文件')
    if not sheets: raise ValueError('没有可导入的数据；首行应为表头，其后至少有一行数据')
    if sum(len(s['rows']) for s in sheets) > _MAX_ROWS: raise ValueError('文件总数据行数不能超过 5000')
    if sum(len(s['headers']) * len(s['rows']) for s in sheets) > 200000: raise ValueError('文件总单元格数不能超过 20 万，请拆分文件')
    return sheets


def _convert(value, column):
    if not value.strip():
        if not column['nullable']:
            raise ValueError('不能为空；若需数据库默认值，请取消该字段映射')
        return None
    kind = column['type'].lower()
    text = value.strip()
    integer = re.match(r'^(tinyint|smallint|mediumint|int|bigint)\b', kind)
    if integer:
        try: number = Decimal(text)
        except InvalidOperation: raise ValueError('须为整数')
        if not number.is_finite() or number != number.to_integral_value(): raise ValueError('须为整数')
        bits = {'tinyint': 8, 'smallint': 16, 'mediumint': 24, 'int': 32, 'bigint': 64}[integer[1]]
        low, high = (0, 2**bits - 1) if 'unsigned' in kind else (-2**(bits-1), 2**(bits-1)-1)
        if number < low or number > high: raise ValueError('整数超出字段范围')
        return int(number)
    decimal = re.match(r'^(?:decimal|numeric)\((\d+),(\d+)\)', kind)
    if decimal:
        try: number = Decimal(text)
        except InvalidOperation: raise ValueError('须为有效小数')
        precision, scale = map(int, decimal.groups())
        if not number.is_finite() or abs(number) >= Decimal(10) ** (precision - scale): raise ValueError('数值超出字段精度')
        if max(0, -number.as_tuple().exponent) > scale: raise ValueError(f'最多允许 {scale} 位小数，不会自动四舍五入')
        if 'unsigned' in kind and number < 0: raise ValueError('不能为负数')
        return str(number)
    if re.match(r'^(float|double|real)\b', kind):
        try: number = float(text)
        except ValueError: raise ValueError('须为数值')
        if not math.isfinite(number) or ('unsigned' in kind and number < 0): raise ValueError('无效数值')
        return number
    if kind == 'date':
        try: result = date.fromisoformat(text[:10]) if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]00:00:00)?', text) else None
        except ValueError: result = None
        if not result or result.year < 1000: raise ValueError('日期格式须为 YYYY-MM-DD，且年份不早于 1000')
        return result.isoformat()
    if kind.startswith(('datetime', 'timestamp')):
        try: result = datetime.fromisoformat(text)
        except ValueError: raise ValueError('日期时间格式须为 YYYY-MM-DD HH:mm:ss')
        if result.tzinfo is not None or result.year < 1000: raise ValueError('请使用不带时区的有效本地日期时间')
        return result.isoformat(sep=' ')
    length = re.match(r'^(?:var)?char\((\d+)\)', kind)
    if length and len(value) > int(length[1]): raise ValueError(f'长度不能超过 {length[1]} 个字符')
    if length or 'text' in kind: return value
    if kind == 'json':
        try: json.loads(value)
        except ValueError: raise ValueError('须为有效 JSON')
        return value
    raise ValueError(f'暂不支持导入此字段类型：{column["type"]}')


def _import_catalog():
    try: schema = list(_schema().values())
    except Exception as exc:
        logger.exception('导入页读取表结构失败')
        raise HTTPException(503, '无法读取业务数据库，请检查查询连接') from exc
    with _lock, _db() as connection:
        history = [_summary(json.loads(row[0])) for row in connection.execute('SELECT payload FROM batches ORDER BY rowid DESC LIMIT 20')]
    return {'tables': schema, 'write_configured': _writer_configured(), 'history': history,
            'max_rows': _MAX_ROWS, 'max_file_bytes': _MAX_BYTES, 'destination': _destination()}


def _stage(filename, raw):
    try: sheets = _parse(filename, raw)
    except Exception as exc:
        raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else '表格解析失败，请检查文件格式或是否加密') from exc
    batch_id = uuid4().hex
    batch = {'id': batch_id, 'filename': filename, 'sheets': sheets, 'status': 'draft',
             'file_hash': hashlib.sha256(raw).hexdigest(), 'created_at': datetime.now().isoformat(),
             'message': '已暂存，尚未写入数据库'}
    with _lock:
        directory = _root() / batch_id
        directory.mkdir(mode=0o700)
        original = directory / ('source' + Path(filename).suffix.lower())
        original.write_bytes(raw)
        original.chmod(0o600)
        _save(batch)
    return batch_id


@router.post('/upload')
async def upload(file: UploadFile = File(...)):
    filename = file.filename or ''
    if not filename or any(char in filename for char in ('/', '\\', '\x00')):
        raise HTTPException(400, '非法文件名')
    if Path(filename).suffix.lower() not in ('.csv', '.xlsx', '.xls'):
        raise HTTPException(400, '仅支持 Excel / CSV 表格')
    raw = bytearray()
    while chunk := await file.read(1024 * 1024):
        raw.extend(chunk)
        if len(raw) > _MAX_BYTES: raise HTTPException(413, '单个文件不能超过 20 MB')
    batch_id = await asyncio.to_thread(_stage, filename, bytes(raw))
    return await asyncio.to_thread(get_batch, batch_id)


@router.get('/batches/{batch_id}')
def get_batch(batch_id: str):
    with _lock: batch = _load(batch_id)
    return {**_summary(batch), 'sheets': [{'name': s['name'], 'headers': s['headers'], 'row_count': len(s['rows']),
                                          'preview': s['rows'][:5]} for s in batch['sheets']]}


class ValidationRequest(BaseModel):
    duplicate_policy: Literal['block','skip'] = 'block'
    table: str = Field(min_length=1, max_length=64)
    sheet: str = Field(min_length=1, max_length=100)
    mapping: dict[str, str]
    connection_id: str = Field(default='default',min_length=1,max_length=48)


def _assess(target,names,prepared,source_rows,connection=None):
    if connection is not None:return duplicates.assessment(connection,target,names,prepared,source_rows)
    if not target.get('unique_keys') and not target.get('dedupe_rule',{}).get('columns'):
        return duplicates.assessment(None,target,names,prepared,source_rows)
    profile=connections.current()
    reader=connections.connect() if profile and profile['id']!='default' else _get_connection()
    try:return duplicates.assessment(reader,target,names,prepared,source_rows)
    finally:reader.close()


def _validate(batch_id: str, body: ValidationRequest):
    with _lock:
        batch = _load(batch_id)
        if batch['status'] in ('completed', 'committing', 'uncertain'):
            raise HTTPException(409, '该批次已写入或结果待核实，不能重新校验导入')
        # 重新校验一开始即撤销旧确认令牌，失败的映射不能继续用旧令牌写入。
        batch.update(status='draft', validation_token=None, message='等待重新校验')
        _save(batch)
        target = _target(body.table)
        sheet = next((s for s in batch['sheets'] if s['name'] == body.sheet), None)
        if not sheet: raise HTTPException(400, '所选工作表不存在')
        columns = {c['name']: c for c in target['columns']}
        if not body.mapping: raise HTTPException(400, '请至少映射一个目标字段')
        for name, header in body.mapping.items():
            if name not in columns or columns[name]['generated'] or header not in sheet['headers']:
                raise HTTPException(400, '字段映射无效，自增和生成字段由数据库填写')
        missing = [c['name'] for c in columns.values() if c['required'] and c['name'] not in body.mapping]
        if missing: raise HTTPException(400, '缺少必填字段映射：' + '、'.join(missing))
        names = list(body.mapping)
        positions = [sheet['headers'].index(body.mapping[name]) for name in names]
        errors, prepared, total_errors = [], [], 0
        for row in sheet['rows']:
            result = []
            for name, position in zip(names, positions):
                try: result.append(_convert(row['cells'][position], columns[name]))
                except ValueError as exc:
                    total_errors += 1
                    if len(errors) < 100:
                        errors.append({'row': row['row'], 'column': name, 'value': row['cells'][position][:120], 'message': str(exc)})
                    result.append(None)
            prepared.append(result)
        fingerprint = hashlib.sha256(json.dumps([batch['file_hash'], _destination(), body.table, body.sheet, body.mapping], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with _db() as connection:
            duplicate = connection.execute("SELECT id FROM batches WHERE fingerprint=? AND id<>? AND status IN ('completed','committing','uncertain') LIMIT 1", (fingerprint, batch_id)).fetchone()
        if duplicate:
            raise HTTPException(409, f'相同文件和映射已有导入批次 {duplicate[0][:8]}，为避免重复追加已阻止，请核对导入记录')
        report = _assess(target,names,prepared,[r['row'] for r in sheet['rows']]) if not errors else None
        valid = not errors and report['conflict']==0 and report['new']>0 and (body.duplicate_policy=='skip' or report['duplicate']==0)
        token = uuid4().hex
        batch.update(status='validated' if valid else 'invalid', table=body.table, sheet=body.sheet,
                     mapping=body.mapping, connection_id=body.connection_id, destination=_destination(), columns=names, prepared=prepared, source_rows=[r['row'] for r in sheet['rows']],
                     row_count=len(prepared), validation_token=token, schema_hash=_schema_hash(target),
                     duplicate_policy=body.duplicate_policy, dedupe_summary=report,
                     fingerprint=fingerprint, message=('查重通过，等待确认写入' if valid else '查重未通过，请核对重复、冲突或待新增数量') if not errors else f'发现 {total_errors} 个错误，尚未写入数据库')
        _save(batch)
        return {'valid': valid, 'dedupe':report, 'duplicate_policy':body.duplicate_policy, 'insert_count':report['new'] if report else 0, 'errors': errors, 'error_count': total_errors, 'row_count': len(prepared),
                'columns': names, 'preview': [prepared[i] for i in report['accepted_indices'][:10]] if report else prepared[:10], 'validation_token': token,
                'unmapped_headers': [h for h in sheet['headers'] if h not in body.mapping.values()]}


class CommitRequest(BaseModel):
    validation_token: str = Field(min_length=32, max_length=32)
    connection_id: str | None = Field(default=None,min_length=1,max_length=48)


def _commit(batch_id: str, body: CommitRequest):
    with _lock:
        batch = _load(batch_id)
        if body.validation_token != batch.get('validation_token'):
            raise HTTPException(409, '校验已失效，请重新校验')
        if batch['status'] == 'completed': return {**_summary(batch), 'replayed': True}
        if batch['status'] != 'validated': raise HTTPException(409, '批次未通过校验，或写入结果待核实，请勿重复提交')
        if batch.get('destination') != _destination():
            raise HTTPException(409, '数据库连接目标已变更，请重新校验')
        if _schema_hash(_target(batch['table'])) != batch['schema_hash']:
            raise HTTPException(409, '目标表结构已变更，请重新校验')
        with _db() as db:
            duplicate = db.execute("SELECT id FROM batches WHERE fingerprint=? AND id<>? AND status IN ('completed','committing','uncertain') LIMIT 1", (batch['fingerprint'], batch_id)).fetchone()
        if duplicate: raise HTTPException(409, '相同文件和映射已提交，请核对历史记录，勿重复导入')
        connection = None
        write_lock = None
        commit_issued = False
        source_row = None
        try:
            connection = _writer()
            cursor = connection.cursor()
            cursor.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO'")
            if 'dedupe_summary' not in batch:raise HTTPException(409,'旧版校验已失效，请重新校验查重')
            # All application import/create writers cooperate on this table lock.
            if batch.get('dedupe_summary',{}).get('rule',{}).get('columns') or _target(batch['table']).get('unique_keys'):
                write_lock=duplicates.table_lock(connection,batch['table']);write_lock.__enter__()
            connection.start_transaction()
            target=_target(batch['table'])
            report=_assess(target,batch['columns'],batch['prepared'],batch['source_rows'],connection)
            if report['signature']!=batch['dedupe_summary']['signature']:
                raise HTTPException(409,'数据库查重结果已变化，请重新校验并核对待新增/跳过数量')
            if report['conflict'] or (batch.get('duplicate_policy')!='skip' and report['duplicate']) or not report['new']:
                raise HTTPException(409,'查重未通过，不能执行写入，请重新校验')
            batch.update(status='committing', message='正在写入，请勿重复提交')
            _save(batch)
            names = ','.join(f'`{name}`' for name in batch['columns'])
            sql = f"INSERT INTO `{batch['table']}` ({names}) VALUES ({','.join(['%s'] * len(batch['columns']))})"
            for index in report['accepted_indices']:
                source_row=batch['source_rows'][index]
                cursor.execute(sql, tuple(batch['prepared'][index]))
            commit_issued = True
            connection.commit()
            batch.update(status='completed', inserted_rows=report['new'], skipped_rows=report['duplicate'], finished_at=datetime.now().isoformat(), message='已成功追加到业务数据库')
            _save(batch)
            return _summary(batch)
        except HTTPException:
            if connection:connection.rollback()
            raise
        except Exception as exc:
            logger.exception('业务数据导入失败 batch=%s', batch_id)
            if commit_issued:
                batch.update(status='uncertain', message='提交结果待核实，请核对数据库和本批次记录，勿重复导入')
            else:
                if connection:
                    try: connection.rollback()
                    except Exception: pass
                messages = {1062: '主键或唯一字段重复', 1452: '外键引用的记录不存在', 1048: '必填字段为空',
                            1406: '字段内容过长', 1264: '数值超出字段范围', 1142: '导入账号没有 INSERT 权限',
                            1045: '导入账号认证失败', 1292: '日期或数值格式不符合数据库约束'}
                message = messages.get(getattr(exc, 'errno', None), '数据库写入失败，请检查连接、权限和字段约束')
                batch.update(status='failed', message=f'{"原表第 " + str(source_row) + " 行：" if source_row else ""}{message}；本批次未提交')
            _save(batch)
            raise HTTPException(409 if not commit_issued else 503, batch['message']) from exc
        finally:
            if write_lock:write_lock.__exit__(None,None,None)
            if connection: connection.close()


@router.delete('/batches/{batch_id}')
def discard(batch_id: str):
    with _lock:
        batch = _load(batch_id)
        if batch['status'] in ('completed', 'committing', 'uncertain'):
            raise HTTPException(409, '已提交或结果待核实的批次须保留用于核对，不能删除')
        directory = _root() / batch_id
        if directory.exists():
            shutil.rmtree(directory)
        with _db() as connection:
            connection.execute('DELETE FROM batches WHERE id=?', (batch_id,))
        return {'status': 'discarded'}


@router.get('/connections')
def import_connection_catalog():
    return {'connections': connections.public_profiles()}


@router.get('')
def import_catalog(connection_id:str='default'):
    with connections.scope(connection_id):
        return {**_import_catalog(),'connections':connections.public_profiles(),'connection_id':connection_id}


@router.post('/batches/{batch_id}/validate')
def validate(batch_id:str,body:ValidationRequest):
    with connections.scope(body.connection_id):return _validate(batch_id,body)


@router.post('/batches/{batch_id}/commit')
def commit(batch_id:str,body:CommitRequest):
    with _lock:
        batch=_load(batch_id);ident=batch.get('connection_id') or 'default'
        if body.connection_id is not None and body.connection_id!=ident:raise HTTPException(409,'所选连接与校验目标不同，请重新校验')
        with connections.scope(ident):return _commit(batch_id,body)
