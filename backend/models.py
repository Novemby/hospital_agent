from sqlalchemy import Column, BigInteger, String, Integer, Date, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from backend.database import Base

# 1. 患者信息表
class Patient(Base):
    __tablename__ = "sys_patient"
    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    gender = Column(Integer, nullable=False)
    birth_date = Column(Date)
    contact_phone = Column(String(20), nullable=False)
    id_card = Column(String(18), unique=True)
    allergy_history = Column(String(255), default="无")
    is_deaf = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

# 2. 医生信息表
class Doctor(Base):
    __tablename__ = "sys_doctor"
    id = Column(BigInteger, primary_key=True, index=True)
    work_no = Column(String(20), unique=True)  # <--- 新增这行
    name = Column(String(50), nullable=False)
    department = Column(String(50), nullable=False)
    title = Column(String(50))
    is_available = Column(Integer, default=1)
    password = Column(String(50), nullable=False, default="123456")

# 3. 科室信息表
class Department(Base):
    __tablename__ = "sys_department"
    id = Column(BigInteger, primary_key=True, index=True)
    dept_code = Column(String(20), unique=True, nullable=False)
    dept_name = Column(String(50), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())


# 4. 排班号源表
class Schedule(Base):
    __tablename__ = "biz_schedule"
    id = Column(BigInteger, primary_key=True, index=True)
    department_id = Column(BigInteger, ForeignKey("sys_department.id"), nullable=False)
    doctor_id = Column(BigInteger, ForeignKey("sys_doctor.id"))
    schedule_date = Column(Date, nullable=False)
    time_period = Column(String(20), nullable=False)  # AM / PM
    capacity_total = Column(Integer, nullable=False, default=20)
    capacity_used = Column(Integer, nullable=False, default=0)
    is_open = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())


# 5. 挂号信息表
class Registration(Base):
    __tablename__ = "biz_registration"
    id = Column(BigInteger, primary_key=True, index=True)
    patient_id = Column(BigInteger, ForeignKey("sys_patient.id"), nullable=False)
    department_id = Column(BigInteger, ForeignKey("sys_department.id"), nullable=False)
    schedule_id = Column(BigInteger, ForeignKey("biz_schedule.id"), nullable=False)
    department = Column(String(50), nullable=False)
    queue_number = Column(String(30), nullable=False)
    current_state = Column(String(20), nullable=False)
    triage_symptoms = Column(Text)
    created_at = Column(DateTime, default=func.now())


# 5.1 科室日号计数表（并发安全发号）
class QueueCounter(Base):
    __tablename__ = "biz_queue_counter"
    id = Column(BigInteger, primary_key=True, index=True)
    department = Column(String(50), nullable=False)
    queue_date = Column(Date, nullable=False)
    current_no = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("department", "queue_date", name="uq_dept_queue_date"),
    )


# 4. 标准电子病历表
class MedicalRecord(Base):
    __tablename__ = "biz_medical_record"
    id = Column(BigInteger, primary_key=True, index=True)
    registration_id = Column(BigInteger, ForeignKey("biz_registration.id"), nullable=False)
    doctor_id = Column(BigInteger, ForeignKey("sys_doctor.id"), nullable=False)
    chief_complaint = Column(Text, nullable=False)
    diagnosis = Column(String(255), nullable=False)
    treatment_plan = Column(Text) # 存储 JSON 格式的处方
    record_time = Column(DateTime, default=func.now())

# 5. 智能体长期记忆摘要表
class AgentMemory(Base):
    __tablename__ = "ai_agent_memory"
    id = Column(BigInteger, primary_key=True, index=True)
    patient_id = Column(BigInteger, ForeignKey("sys_patient.id"), nullable=False)
    registration_id = Column(BigInteger, ForeignKey("biz_registration.id"), nullable=False)
    session_id = Column(String(64), nullable=False)
    ai_summary = Column(Text, nullable=False)
    vector_db_id = Column(String(64))
    created_at = Column(DateTime, default=func.now())

# 6. 医患对话记录表
class ChatMessage(Base):
    __tablename__ = "biz_chat_message"
    id = Column(BigInteger, primary_key=True, index=True)
    registration_id = Column(BigInteger, ForeignKey("biz_registration.id"), nullable=False)
    sender_type = Column(String(10), nullable=False) # 'pat' 或 'doc'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())