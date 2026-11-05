-- 仅作用于本项目 Docker 开发库的新建账号。
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'tanyuan_reader'@'%';
GRANT SELECT, SHOW VIEW ON department_workbench.* TO 'tanyuan_reader'@'%';
