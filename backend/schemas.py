from datetime import date
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from enum import Enum

# 医生登录
class DoctorLoginRequest(BaseModel):
    work_no: str
    password: str

class DoctorOut(BaseModel):
    id: int
    work_no: str
    name: str
    department: str
    title: Optional[str] = None

# 患者相关
class PatientOut(BaseModel):
    id: int
    name: str
    gender: int
    birth_date: Optional[date] = None
    contact_phone: Optional[str] = None
    id_card: Optional[str] = None
    allergy_history: Optional[str] = None
    is_deaf: bool
    dept: Optional[str] = None   # 最近挂号科室
    registration_id: Optional[int] = None
    current_state: Optional[str] = None
    triage_struct: Optional[Dict[str, Any]] = None  # Agent结构化分诊槽位（若有）

class PatientLoginOut(BaseModel):
    id: int
    name: str
    is_deaf: bool
    allergy_history: Optional[str]
    id_card: str

# 挂号
class RegistrationOut(BaseModel):
    id: int
    patient_id: int
    department: str
    queue_number: str
    current_state: str
    triage_symptoms: Optional[str] = None

class RegistrationSubmitRequest(BaseModel):
    patient_id: int
    department: str
    schedule_id: Optional[int] = None
    triage_symptoms: Optional[str] = None


class RegistrationStateUpdateRequest(BaseModel):
    current_state: str

class SlotInfo(BaseModel):
    slot_id: int
    doctor_id: Optional[int]
    doctor_name: str
    department: str
    date: str
    period: str
    remain: int
    capacity_total: int
    capacity_used: int

class SlotsResponse(BaseModel):
    department: str
    department_id: int
    date: str
    slots: List[SlotInfo]

# Agent 对话
class RegistrationAgentState(str, Enum):
    INIT = "INIT"
    COLLECT_INFO = "COLLECT_INFO"
    QUERY_SLOTS = "QUERY_SLOTS"
    CONFIRM = "CONFIRM"
    SUBMIT = "SUBMIT"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

class RegistrationAgentChatRequest(BaseModel):
    session_id: str
    patient_id: int
    message: str

class AgentResponse(BaseModel):
    reply: str
    state: str
    required_fields: List[str]
    options: List
    next_action: Optional[str]
    slots: dict

# 语音识别
class SpeechToTextResponse(BaseModel):
    success: bool
    text: Optional[str] = None
    error: Optional[str] = None
