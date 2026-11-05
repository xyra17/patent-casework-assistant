export function importFileError(file) {
 if(!/\.(xlsx|xls|csv)$/i.test(file.name))return '请选择 XLSX、XLS 或 CSV 表格'
 if(file.size>20*1024*1024)return '单个文件不能超过 20 MB'
 if(file.size===0)return '不能上传空文件'
 return ''
}
