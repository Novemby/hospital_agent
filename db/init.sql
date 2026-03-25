-- 初始化数据库与测试数据
CREATE DATABASE IF NOT EXISTS medical_agent_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE medical_agent_db;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS biz_chat_message;
DROP TABLE IF EXISTS ai_agent_memory;
DROP TABLE IF EXISTS biz_medical_record;
DROP TABLE IF EXISTS biz_registration;
DROP TABLE IF EXISTS biz_schedule;
DROP TABLE IF EXISTS biz_queue_counter;
DROP TABLE IF EXISTS sys_department;
DROP TABLE IF EXISTS sys_doctor;
DROP TABLE IF EXISTS sys_patient;
SET FOREIGN_KEY_CHECKS = 1;

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

-- 插入科室与排班测试数据 (总计 18 个科室)
INSERT INTO sys_department (id, dept_code, dept_name, is_active) VALUES
(1, 'GK', '骨科', 1),
(2, 'FR', '发热门诊', 1),
(3, 'TJ', '体检中心', 1),
(4, 'EBH', '耳鼻喉科', 1),
(5, 'NK', '内科', 1),
(6, 'WK', '外科', 1),
(7, 'EK', '儿科', 1),
(8, 'FK', '妇科', 1),
(9, 'SNK', '神经内科', 1),
(10, 'PFK', '皮肤科', 1),
(11, 'XWNK', '消化内科', 1),
(12, 'HXNK', '呼吸内科', 1),
(13, 'XXGNK', '心血管内科', 1),
(14, 'MNWK', '泌尿外科', 1),
(15, 'YK', '眼科', 1),
(16, 'KQK', '口腔科', 1),
(17, 'JSXLK', '精神心理科', 1),
(18, 'JZK', '急诊科', 1);

-- 插入医生测试数据（每个科室2名医生，共计36名）
INSERT INTO sys_doctor (id, work_no, name, department, title, is_available, password) VALUES
(1, 'd001', '张伟', '骨科', '主任医师', 1, '123456'),
(2, 'd002', '刘洋', '骨科', '主治医师', 1, '123456'),
(3, 'd003', '李敏', '发热门诊', '副主任医师', 1, '123456'),
(4, 'd004', '周倩', '发热门诊', '主治医师', 1, '123456'),
(5, 'd005', '王芳', '体检中心', '主治医师', 1, '123456'),
(6, 'd006', '陈晨', '体检中心', '住院医师', 1, '123456'),
(7, 'd007', '赵宁', '耳鼻喉科', '主任医师', 1, '123456'),
(8, 'd008', '孙悦', '耳鼻喉科', '主治医师', 1, '123456'),
(9, 'd009', '高峰', '内科', '主任医师', 1, '123456'),
(10, 'd010', '马琳', '内科', '主治医师', 1, '123456'),
(11, 'd011', '吴磊', '外科', '副主任医师', 1, '123456'),
(12, 'd012', '何静', '外科', '主治医师', 1, '123456'),
(13, 'd013', '郑凯', '儿科', '主任医师', 1, '123456'),
(14, 'd014', '宋佳', '儿科', '主治医师', 1, '123456'),
(15, 'd015', '潘虹', '妇科', '副主任医师', 1, '123456'),
(16, 'd016', '吕婷', '妇科', '主治医师', 1, '123456'),
(17, 'd017', '蒋涛', '神经内科', '主任医师', 1, '123456'),
(18, 'd018', '韩雪', '神经内科', '主治医师', 1, '123456'),
(19, 'd019', '彭伟', '皮肤科', '副主任医师', 1, '123456'),
(20, 'd020', '罗敏', '皮肤科', '主治医师', 1, '123456'),
(21, 'd021', '苏强', '消化内科', '主任医师', 1, '123456'),
(22, 'd022', '林婉', '消化内科', '主治医师', 1, '123456'),
(23, 'd023', '高健', '呼吸内科', '副主任医师', 1, '123456'),
(24, 'd024', '叶萍', '呼吸内科', '主治医师', 1, '123456'),
(25, 'd025', '顾城', '心血管内科', '主任医师', 1, '123456'),
(26, 'd026', '孟洁', '心血管内科', '主治医师', 1, '123456'),
(27, 'd027', '梁宇', '泌尿外科', '副主任医师', 1, '123456'),
(28, 'd028', '许晴', '泌尿外科', '主治医师', 1, '123456'),
(29, 'd029', '方明', '眼科', '主任医师', 1, '123456'),
(30, 'd030', '董越', '眼科', '主治医师', 1, '123456'),
(31, 'd031', '陆毅', '口腔科', '副主任医师', 1, '123456'),
(32, 'd032', '沈瑶', '口腔科', '主治医师', 1, '123456'),
(33, 'd033', '白涛', '精神心理科', '主任医师', 1, '123456'),
(34, 'd034', '夏雪', '精神心理科', '主治医师', 1, '123456'),
(35, 'd035', '雷霆', '急诊科', '副主任医师', 1, '123456'),
(36, 'd036', '莫愁', '急诊科', '主治医师', 1, '123456');

-- 插入患者测试数据
SET NAMES utf8mb4;
INSERT INTO sys_patient (id, name, gender, birth_date, contact_phone, id_card, allergy_history, is_deaf) VALUES
(1001, '张三', 1, '1970-05-15', '13800138001', '123456', '无', 1),
(1002, '李四', 1, '1992-08-20', '13900139002', '234567', '青霉素', 0),
(1003, '王五', 1, '1985-11-11', '13700137003', '345678', '无', 0);

-- 每个科室两人，分别负责上午和下午的排班号源
INSERT INTO biz_schedule (id, department_id, doctor_id, schedule_date, time_period, capacity_total, capacity_used, is_open) VALUES
(20001, 1, 1, CURDATE(), 'AM', 50, 1, 1),
(20002, 1, 2, CURDATE(), 'PM', 50, 3, 1),
(20003, 2, 3, CURDATE(), 'AM', 50, 2, 1),
(20004, 2, 4, CURDATE(), 'PM', 50, 1, 1),
(20005, 3, 5, CURDATE(), 'AM', 40, 2, 1),
(20006, 3, 6, CURDATE(), 'PM', 40, 4, 1),
(20007, 4, 7, CURDATE(), 'AM', 35, 2, 1),
(20008, 4, 8, CURDATE(), 'PM', 35, 3, 1),
(20009, 5, 9, CURDATE(), 'AM', 45, 5, 1),
(20010, 5, 10, CURDATE(), 'PM', 45, 4, 1),
(20011, 6, 11, CURDATE(), 'AM', 30, 2, 1),
(20012, 6, 12, CURDATE(), 'PM', 30, 3, 1),
(20013, 7, 13, CURDATE(), 'AM', 40, 3, 1),
(20014, 7, 14, CURDATE(), 'PM', 40, 2, 1),
(20015, 8, 15, CURDATE(), 'AM', 30, 1, 1),
(20016, 8, 16, CURDATE(), 'PM', 30, 1, 1),
(20017, 9, 17, CURDATE(), 'AM', 35, 2, 1),
(20018, 9, 18, CURDATE(), 'PM', 35, 2, 1),
(20019, 10, 19, CURDATE(), 'AM', 30, 2, 1),
(20020, 10, 20, CURDATE(), 'PM', 30, 2, 1),
(20021, 11, 21, CURDATE(), 'AM', 40, 5, 1),
(20022, 11, 22, CURDATE(), 'PM', 40, 2, 1),
(20023, 12, 23, CURDATE(), 'AM', 35, 4, 1),
(20024, 12, 24, CURDATE(), 'PM', 35, 1, 1),
(20025, 13, 25, CURDATE(), 'AM', 30, 6, 1),
(20026, 13, 26, CURDATE(), 'PM', 30, 2, 1),
(20027, 14, 27, CURDATE(), 'AM', 25, 1, 1),
(20028, 14, 28, CURDATE(), 'PM', 25, 0, 1),
(20029, 15, 29, CURDATE(), 'AM', 40, 8, 1),
(20030, 15, 30, CURDATE(), 'PM', 40, 3, 1),
(20031, 16, 31, CURDATE(), 'AM', 30, 2, 1),
(20032, 16, 32, CURDATE(), 'PM', 30, 1, 1),
(20033, 17, 33, CURDATE(), 'AM', 20, 1, 1),
(20034, 17, 34, CURDATE(), 'PM', 20, 0, 1),
(20035, 18, 35, CURDATE(), 'AM', 50, 10, 1),
(20036, 18, 36, CURDATE(), 'PM', 50, 5, 1);

-- 插入挂号流水数据
INSERT INTO biz_registration (id, patient_id, department_id, schedule_id, department, queue_number, current_state, triage_symptoms) VALUES
(10001, 1001, 1, 20001, '骨科', 'GK20260324-001', 'IN_CONSULTATION', '左边肩膀酸疼，手抬不起，持续一周'),
(10002, 1002, 2, 20003, '发热门诊', 'FR20260324-001', 'WAITING_TRIAGE', '头痛、发热两天');

-- 插入病历数据
INSERT INTO biz_medical_record (id, registration_id, doctor_id, chief_complaint, diagnosis, treatment_plan) VALUES
(50001, 10001, 1, '左边肩膀酸疼，手抬不起，持续一周', '肩周炎', '[{"drug": "布洛芬", "dosage": "200mg", "usage": "每日两次"}]');

-- 插入医患对话记录
INSERT INTO biz_chat_message (registration_id, sender_type, content) VALUES
(10001, 'pat', '[手语主诉] 我感觉左边肩膀酸疼得很厉害，手抬不起来，已经持续一周了。'),
(10001, 'doc', '是活动时疼痛加重，还是休息时也疼？'),
(10001, 'pat', '[手语] 抬手的时候特别疼，晚上睡觉有时也会疼醒。');