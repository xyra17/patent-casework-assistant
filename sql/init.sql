SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS department_workbench DEFAULT CHARSET utf8mb4;
USE department_workbench;

-- 案件信息只读投影；原件同步后填充，不预置无关行业数据。
CREATE TABLE IF NOT EXISTS cw_cases (
 case_key VARCHAR(160) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_id VARCHAR(100) NOT NULL,
 title TEXT,client_alias VARCHAR(255),client_case_id VARCHAR(255),owner_name VARCHAR(100),technical_domain VARCHAR(100),stage VARCHAR(100),
 received_date DATE,draft_due_date DATE,client_review_due_date DATE,filing_due_date DATE,actual_filed_date DATE,
 filing_verified BOOLEAN NOT NULL,ipr_contact VARCHAR(255),application_type VARCHAR(100),commission_type VARCHAR(100),
 important VARCHAR(100),urgent VARCHAR(100),notes TEXT,conflict_fields JSON NOT NULL,source_refs JSON NOT NULL,generation BIGINT NOT NULL,
 INDEX(root_id),INDEX(case_id),INDEX(received_date),INDEX(actual_filed_date),INDEX(owner_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cw_documents (
 document_id VARCHAR(64) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_key VARCHAR(160),case_id VARCHAR(100),
 relative_path TEXT NOT NULL,kind VARCHAR(40),revision VARCHAR(64),parser_key VARCHAR(64),state VARCHAR(40),generation BIGINT NOT NULL,INDEX(root_id),INDEX(case_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cw_meetings (
 meeting_id VARCHAR(64) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_key VARCHAR(160),case_id VARCHAR(100),client_alias VARCHAR(255),
 meeting_date DATE,iprs JSON NOT NULL,agents JSON NOT NULL,inventors JSON NOT NULL,content TEXT,pending TEXT,changes TEXT,
 existing_art TEXT,closest_art TEXT,distinguishing_features TEXT,comparison TEXT,primary_points TEXT,secondary_points TEXT,
 source_refs JSON NOT NULL,generation BIGINT NOT NULL,INDEX(root_id),INDEX(case_key),INDEX(meeting_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cw_tags (
 tag_id VARCHAR(64) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_key VARCHAR(160) NOT NULL,tag VARCHAR(100) NOT NULL,
 method VARCHAR(100),source_refs JSON NOT NULL,generation BIGINT NOT NULL,INDEX(root_id),INDEX(case_key),INDEX(tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cw_sync_state (
 root_id VARCHAR(32) PRIMARY KEY,label VARCHAR(100),generation BIGINT NOT NULL,classification VARCHAR(40),
 state VARCHAR(40),last_scan VARCHAR(80),snapshot_hash VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS manual_case_notes (note_id BIGINT PRIMARY KEY AUTO_INCREMENT, case_id VARCHAR(100) NOT NULL, note_type VARCHAR(100) NOT NULL DEFAULT '业务补充', note_text TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX(case_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
