"""Bounded attachment staging. Originals are replaced only after every upload is complete."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from fastapi import HTTPException

EXTENSIONS = {'.csv', '.xlsx', '.xls', '.txt', '.md', '.doc', '.docx', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
MAX_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_FILES = 20


async def save_uploads(directory: Path, files, replace: str = '[]') -> dict:
    try:
        replacements = json.loads(replace)
        if not isinstance(replacements, list) or any(not isinstance(n, str) for n in replacements):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(400, '替换清单必须是文件名数组')
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, '每次请选择 1～20 个附件')
    names = [f.filename for f in files]
    for name in names:
        if not name or name in ('.', '..') or any(c in name for c in ('/', '\\', '\x00')):
            raise HTTPException(400, '非法文件名')
        if Path(name).suffix.lower() not in EXTENSIONS:
            raise HTTPException(400, '支持 CSV、Excel、文本、Word、PDF 和图片')
    if len(set(names)) != len(names):
        raise HTTPException(400, '本批次有重复文件名，请分别上传')
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise HTTPException(403, '附件必须是普通文件')
    conflicts = [n for n in names if (directory / n).exists() and n not in replacements]
    if conflicts:
        raise HTTPException(409, {'code': 'attachment_conflict', 'message': '存在同名附件，请确认是否替换', 'files': conflicts})
    saved = []
    # Staging on the same filesystem makes os.replace atomic for each file.
    with tempfile.TemporaryDirectory(prefix='.upload-', dir=directory) as temp:
        total = 0
        for file in files:
            size = 0
            with (Path(temp) / file.filename).open('wb') as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_BYTES or total > MAX_BATCH_BYTES:
                        raise HTTPException(413, '单个附件不得超过 20MB，每批不得超过 100MB')
                    output.write(chunk)
                if size == 0:
                    raise HTTPException(400, f'附件不能为空：{file.filename}')
                output.flush()
                os.fsync(output.fileno())
        try:
            for name in names:
                os.replace(Path(temp) / name, directory / name)
                saved.append(name)
        except OSError as exc:
            raise HTTPException(500, {'code': 'attachment_publish_failed', 'message': '部分附件保存失败，请刷新列表核对后重试', 'saved': saved}) from exc
    return {'status': 'uploaded', 'files': saved}
