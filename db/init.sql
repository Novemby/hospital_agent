-- 初始化数据库与测试数据

CREATE DATABASE IF NOT EXISTS medical_agent_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE medical_agent_db;

-- 表结构
CREATE TABLE IF NOT EXISTS sys_patient (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '患者唯一标识符',
    name VARCHAR(50) NOT NULL COMMENT '患者真实姓名',
    gender TINYINT NOT NULL COMMENT '性别 (0:女, 1:男)',
    birth_date DATE COMMENT '出生年月',
    contact_phone VARCHAR(20) NOT NULL COMMENT '紧急联系电话',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '档案创建时间',
    id_card VARCHAR(18) UNIQUE COMMENT '身份证号',
    allergy_history VARCHAR(255) DEFAULT '无' COMMENT '过敏史',
    is_deaf TINYINT NOT NULL DEFAULT 0 COMMENT '是否听障/聋哑 (0:否, 1:是)'
);

CREATE TABLE IF NOT EXISTS sys_doctor (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '医生唯一标识符',
    work_no VARCHAR(20) UNIQUE COMMENT '医生工号',
    name VARCHAR(50) NOT NULL COMMENT '医生姓名',
    department VARCHAR(50) NOT NULL COMMENT '所属科室',
    title VARCHAR(50) COMMENT '医生职称',
    is_available TINYINT NOT NULL DEFAULT 1 COMMENT '当前排班及接诊状态 (0:休息, 1:可接诊)',
    password VARCHAR(50) NOT NULL DEFAULT '123456' COMMENT '登录密码'
);

CREATE TABLE IF NOT EXISTS sys_department (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '科室唯一ID',
    dept_code VARCHAR(20) NOT NULL UNIQUE COMMENT '科室编码',
    dept_name VARCHAR(50) NOT NULL UNIQUE COMMENT '科室名称',
    is_active TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
);

CREATE TABLE IF NOT EXISTS biz_schedule (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '排班号源ID',
    department_id BIGINT NOT NULL COMMENT '关联科室ID',
    doctor_id BIGINT NULL COMMENT '关联医生ID，可空',
    schedule_date DATE NOT NULL COMMENT '排班日期',
    time_period VARCHAR(20) NOT NULL COMMENT '时间段，如 AM/PM',
    capacity_total INT NOT NULL DEFAULT 20 COMMENT '总号源',
    capacity_used INT NOT NULL DEFAULT 0 COMMENT '已使用号源',
    is_open TINYINT NOT NULL DEFAULT 1 COMMENT '是否开放挂号',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    FOREIGN KEY (department_id) REFERENCES sys_department(id),
    FOREIGN KEY (doctor_id) REFERENCES sys_doctor(id)
);

CREATE TABLE IF NOT EXISTS biz_registration (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '挂号单据全局唯一ID',
    patient_id BIGINT NOT NULL COMMENT '关联患者表',
    department_id BIGINT NOT NULL COMMENT '关联科室ID',
    schedule_id BIGINT NOT NULL COMMENT '关联号源ID',
    department VARCHAR(50) NOT NULL COMMENT '患者挂号的日间科室（冗余字段）',
    queue_number VARCHAR(30) NOT NULL COMMENT '系统分配的排队序号',
    current_state VARCHAR(20) NOT NULL COMMENT '状态机节点标识',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '挂号业务发生时间',
    triage_symptoms TEXT COMMENT '分诊初始主诉',
    FOREIGN KEY (patient_id) REFERENCES sys_patient(id),
    FOREIGN KEY (department_id) REFERENCES sys_department(id),
    FOREIGN KEY (schedule_id) REFERENCES biz_schedule(id)
);

CREATE TABLE IF NOT EXISTS biz_queue_counter (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '计数器唯一ID',
    department VARCHAR(50) NOT NULL COMMENT '科室名称',
    queue_date DATE NOT NULL COMMENT '排队日期',
    current_no INT NOT NULL DEFAULT 0 COMMENT '当前已发号码',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uq_dept_queue_date (department, queue_date)
);

CREATE TABLE IF NOT EXISTS biz_medical_record (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '病历记录全局唯一ID',
    registration_id BIGINT NOT NULL COMMENT '关联本次挂号流水',
    doctor_id BIGINT NOT NULL COMMENT '关联接诊医生',
    chief_complaint TEXT NOT NULL COMMENT '主诉',
    diagnosis VARCHAR(255) NOT NULL COMMENT '临床确诊结果',
    treatment_plan TEXT COMMENT '治疗方案与书面医嘱',
    record_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '电子病历最终归档与签名时间',
    FOREIGN KEY (registration_id) REFERENCES biz_registration(id),
    FOREIGN KEY (doctor_id) REFERENCES sys_doctor(id)
);

CREATE TABLE IF NOT EXISTS ai_agent_memory (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '记忆区块唯一ID',
    patient_id BIGINT NOT NULL COMMENT '关联患者表',
    registration_id BIGINT NOT NULL COMMENT '关联产生该记忆的挂号流水',
    session_id VARCHAR(64) NOT NULL COMMENT '关联Redis中存储的当次会话流水号',
    ai_summary TEXT NOT NULL COMMENT '大模型生成的本次就诊自然语言结构化摘要',
    vector_db_id VARCHAR(64) COMMENT '关联向量数据库中对应的多维向量ID',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记忆生成与落盘时间',
    FOREIGN KEY (patient_id) REFERENCES sys_patient(id),
    FOREIGN KEY (registration_id) REFERENCES biz_registration(id)
);

CREATE TABLE IF NOT EXISTS biz_chat_message (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '消息唯一ID',
    registration_id BIGINT NOT NULL COMMENT '关联本次挂号流水',
    sender_type VARCHAR(10) NOT NULL COMMENT '发送方类型 (pat: 患者, doc: 医生)',
    content TEXT NOT NULL COMMENT '消息内容',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '消息发送时间',
    FOREIGN KEY (registration_id) REFERENCES biz_registration(id)
);

-- 清空旧数据（便于重复执行）
SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE biz_chat_message;
TRUNCATE TABLE ai_agent_memory;
TRUNCATE TABLE biz_medical_record;
TRUNCATE TABLE biz_registration;
TRUNCATE TABLE biz_schedule;
TRUNCATE TABLE biz_queue_counter;
TRUNCATE TABLE sys_department;
TRUNCATE TABLE sys_doctor;
TRUNCATE TABLE sys_patient;
SET FOREIGN_KEY_CHECKS = 1;

-- 插入医生测试数据
INSERT INTO sys_doctor (id, work_no, name, department, title, is_available, password) VALUES
(1, 'd001', '张伟', '骨科', '主任医师', 1, '123456'),
(2, 'd002', '李敏', '发热门诊', '副主任医师', 1, '123456'),
(3, 'd003', '王芳', '体检中心', '主治医师', 1, '123456');

-- 插入患者测试数据
SET NAMES utf8mb4;
INSERT INTO sys_patient (id, name, gender, birth_date, contact_phone, id_card, allergy_history, is_deaf) VALUES
(1001, '张三', 1, '1970-05-15', '13800138001', '123456', '无', 1),
(1002, '李四', 1, '1992-08-20', '13900139002', '234567', '青霉素', 0),
(1003, '王五', 1, '1985-11-11', '13700137003', '345678', '无', 0);

-- 插入科室与排班测试数据
INSERT INTO sys_department (id, dept_code, dept_name, is_active) VALUES
(1, 'GK', '骨科', 1),
(2, 'FR', '发热门诊', 1),
(3, 'TJ', '体检中心', 1),
(4, 'EBH', '耳鼻喉科', 1),
(5, 'NK', '内科', 1);

INSERT INTO biz_schedule (id, department_id, doctor_id, schedule_date, time_period, capacity_total, capacity_used, is_open) VALUES
(20001, 1, 1, CURDATE(), 'AM', 50, 1, 1),
(20002, 2, 2, CURDATE(), 'AM', 50, 1, 1);

-- 插入挂号流水数据
INSERT INTO biz_registration (id, patient_id, department_id, schedule_id, department, queue_number, current_state, triage_symptoms) VALUES
(10001, 1001, 1, 20001, '骨科', 'GK20260324-001', 'IN_CONSULTATION', '左边肩膀酸疼，手抬不起，持续一周'),
(10002, 1002, 2, 20002, '发热门诊', 'FR20260324-001', 'WAITING_TRIAGE', '头痛、发热两天');

-- 插入病历数据
INSERT INTO biz_medical_record (id, registration_id, doctor_id, chief_complaint, diagnosis, treatment_plan) VALUES
(50001, 10001, 1, '左边肩膀酸疼，手抬不起，持续一周', '肩周炎', '[{"drug": "布洛芬", "dosage": "200mg", "usage": "每日两次"}]');

-- 插入医患对话记录
INSERT INTO biz_chat_message (registration_id, sender_type, content) VALUES
(10001, 'pat', '[手语主诉] 我感觉左边肩膀酸疼得很厉害，手抬不起来，已经持续一周了。'),
(10001, 'doc', '是活动时疼痛加重，还是休息时也疼？'),
(10001, 'pat', '[手语] 抬手的时候特别疼，晚上睡觉有时也会疼醒。');

