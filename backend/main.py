from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from backend.database import SessionLocal, engine
from backend import models
from pydantic import BaseModel
import requests
import base64
import time

from pydub import AudioSegment
import io


# --- 定义接收前端 JSON 数据的格式 ---
class DoctorLoginRequest(BaseModel):
    work_no: str
    password: str


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


class RegistrationSubmitRequest(BaseModel):
    patient_id: int
    department: str
    triage_symptoms: Optional[str] = None


# 简化版内存会话（后续可替换为 Redis）
agent_sessions: Dict[str, dict] = {}

DEPARTMENTS = ["骨科", "发热门诊", "体检中心", "耳鼻喉科", "内科"]


def detect_department(text: str) -> Optional[str]:
    for dept in DEPARTMENTS:
        if dept in text:
            return dept
    return None


def detect_date(text: str) -> Optional[str]:
    if "今天" in text:
        return datetime.now().strftime("%Y-%m-%d")
    if "明天" in text:
        return datetime.now().strftime("%Y-%m-%d")
    return None


def get_or_create_agent_session(session_id: str, patient_id: int) -> dict:
    if session_id not in agent_sessions:
        agent_sessions[session_id] = {
            "patient_id": patient_id,
            "state": RegistrationAgentState.INIT.value,
            "slots": {
                "department": None,
                "date": None,
                "triage_symptoms": None,
            },
            "last_options": [],
        }
    return agent_sessions[session_id]


def make_agent_response(reply: str, session: dict, required_fields=None, options=None, next_action=None):
    return {
        "reply": reply,
        "state": session["state"],
        "required_fields": required_fields or [],
        "options": options or [],
        "next_action": next_action,
        "slots": session["slots"],
    }


# 自动在数据库中创建表（如果表已存在则忽略）
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="无障碍智慧医疗辅助系统 API")

# 配置 CORS，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段允许所有地址，生产环境应严格限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 百度语音识别配置 ====================
BAIDU_API_KEY = "lwUYmBdFPhZxU7ESNir7mF75"  # 请替换为实际值
BAIDU_SECRET_KEY = "Ny4uiiKwbr1k21yt2iM0SFz6hWo0hwId"  # 请替换为实际值
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_SPEECH_URL = "https://vop.baidu.com/server_api"

_baidu_token = None
_token_expires = 0


def get_baidu_token():
    """获取百度access_token，带缓存"""
    global _baidu_token, _token_expires
    if _baidu_token and time.time() < _token_expires:
        return _baidu_token
    try:
        params = {
            "grant_type": "client_credentials",
            "client_id": BAIDU_API_KEY,
            "client_secret": BAIDU_SECRET_KEY
        }
        resp = requests.post(BAIDU_TOKEN_URL, params=params)
        if resp.status_code == 200:
            data = resp.json()
            _baidu_token = data.get("access_token")
            # 有效期默认为30天，这里设置28天余量
            _token_expires = time.time() + 28 * 24 * 3600
            return _baidu_token
        else:
            raise Exception(f"获取百度token失败，状态码：{resp.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音识别服务初始化失败: {str(e)}")


# ==================== 语音转文字接口 ====================
@app.post("/speech-to-text")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    语音转文字接口：自动将任意音频格式转换为 PCM 16k 16bit mono
    """
    try:
        # 读取上传的音频内容
        audio_bytes = await audio.read()
        if len(audio_bytes) == 0:
            raise HTTPException(status_code=400, detail="音频文件为空")

        # 使用 pydub 加载音频（会自动根据扩展名/内容识别格式）
        audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes))

        # 转换为单声道、16kHz、16bit PCM
        audio_segment = audio_segment.set_channels(1).set_frame_rate(16000).set_sample_width(2)

        # 导出 PCM 原始数据
        pcm_data = audio_segment.raw_data

        # Base64 编码
        audio_base64 = base64.b64encode(pcm_data).decode('utf-8')

        token = get_baidu_token()

        data = {
            "format": "pcm",
            "rate": 16000,
            "channel": 1,
            "cuid": "fastapi_server",
            "token": token,
            "speech": audio_base64,
            "len": len(pcm_data)
        }
        headers = {"Content-Type": "application/json"}
        resp = requests.post(BAIDU_SPEECH_URL, json=data, headers=headers, timeout=10)
        result = resp.json()

        if result.get("err_no") == 0:
            text = result["result"][0]
            return {"success": True, "text": text}
        else:
            # 返回详细错误信息，便于前端调试
            return {"success": False, "error": result.get("err_msg", "未知错误")}
    except Exception as e:
        # 捕获所有异常并返回友好错误
        raise HTTPException(status_code=500, detail=f"语音识别服务错误: {str(e)}")

# ==================== 测试端点（可选）====================
@app.get("/test")
def test():
    return {"message": "后端服务正常运行"}


# ==================== 依赖注入：获取数据库会话 ====================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==================== 新增：患者列表接口 ====================
@app.get("/api/patients")
def get_patients(dept: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """
    获取患者列表（可按科室筛选）
    若提供科室，则返回该科室下有挂号记录的患者（去重）
    若不提供科室，则返回所有患者
    """
    if dept:
        # 查询该科室下有过挂号记录的患者
        patients = db.query(models.Patient).join(
            models.Registration,
            models.Patient.id == models.Registration.patient_id
        ).filter(models.Registration.department == dept).distinct().all()
    else:
        patients = db.query(models.Patient).all()

    result = []
    for p in patients:
        # 获取该患者最近一次挂号的科室（用于前端显示）
        latest_reg = db.query(models.Registration).filter(
            models.Registration.patient_id == p.id
        ).order_by(models.Registration.created_at.desc()).first()
        dept_name = latest_reg.department if latest_reg else None

        result.append({
            "id": p.id,
            "name": p.name,
            "gender": p.gender,
            "birth_date": p.birth_date,
            "contact_phone": p.contact_phone,
            "id_card": p.id_card,
            "allergy_history": p.allergy_history,
            "is_deaf": p.is_deaf,
            "dept": dept_name,  # 最近挂号科室
        })
    return result


# ==================== 原有 API 路由 ====================
@app.get("/api/patients/{patient_id}")
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    """根据 ID 获取患者基本信息"""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="未找到该患者档案")
    return {
        "id": patient.id,
        "name": patient.name,
        "gender": patient.gender,
        "birth_date": patient.birth_date,
        "contact_phone": patient.contact_phone,
        "id_card": patient.id_card,
        "allergy_history": patient.allergy_history,
        "is_deaf": patient.is_deaf
    }


@app.get("/api/doctors/{doctor_id}")
def get_doctor(doctor_id: int, db: Session = Depends(get_db)):
    """根据 ID 获取医生基本信息（用于医生登录验证）"""
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="未找到该医生信息")
    return {
        "id": doctor.id,
        "work_no": doctor.work_no,
        "name": doctor.name,
        "department": doctor.department,
        "title": doctor.title,
        "is_available": doctor.is_available
    }


@app.get("/api/patients/{patient_id}/registrations")
def get_patient_registrations(patient_id: int, db: Session = Depends(get_db)):
    """获取指定患者的所有挂号流水及当前状态"""
    registrations = db.query(models.Registration).filter(models.Registration.patient_id == patient_id).all()
    return [
        {
            "id": reg.id,
            "patient_id": reg.patient_id,
            "department": reg.department,
            "queue_number": reg.queue_number,
            "current_state": reg.current_state,
            "triage_symptoms": reg.triage_symptoms
        }
        for reg in registrations
    ]


@app.get("/api/patients/by-idcard/{id_card}")
def get_patient_by_idcard(id_card: str, db: Session = Depends(get_db)):
    """患者登录：通过身份证号查询患者档案"""
    patient = db.query(models.Patient).filter(models.Patient.id_card == id_card).first()
    if not patient:
        raise HTTPException(status_code=404, detail="未找到该身份证对应的患者，请先建档")
    return {
        "id": patient.id,
        "name": patient.name,
        "is_deaf": patient.is_deaf,
        "allergy_history": patient.allergy_history,
        "id_card": patient.id_card
    }


@app.post("/api/doctors/login")
def doctor_login(login_data: DoctorLoginRequest, db: Session = Depends(get_db)):
    """医生登录"""
    doctor = db.query(models.Doctor).filter(models.Doctor.work_no == login_data.work_no).first()
    if not doctor or doctor.password != login_data.password:
        raise HTTPException(status_code=401, detail="工号或密码错误")
    return {
        "id": doctor.id,
        "work_no": doctor.work_no,
        "name": doctor.name,
        "department": doctor.department,
        "title": doctor.title
    }


@app.get("/api/registration/departments")
def get_registration_departments():
    """挂号可选科室列表"""
    return {"departments": DEPARTMENTS}


@app.get("/api/registration/slots")
def get_registration_slots(department: str = Query(...), date: str = Query(...), db: Session = Depends(get_db)):
    """查询科室可用医生与模拟号源"""
    doctors = db.query(models.Doctor).filter(
        models.Doctor.department == department,
        models.Doctor.is_available == 1
    ).all()

    slots = []
    for i, doctor in enumerate(doctors, start=1):
        slots.append({
            "slot_id": f"{department}-{date}-{i}",
            "doctor_id": doctor.id,
            "doctor_name": doctor.name,
            "department": doctor.department,
            "date": date,
            "period": "上午" if i % 2 else "下午",
            "remain": max(0, 10 - i),
        })

    return {
        "department": department,
        "date": date,
        "slots": slots,
    }


@app.post("/api/registration/submit")
def submit_registration(payload: RegistrationSubmitRequest, db: Session = Depends(get_db)):
    """提交挂号（MVP版本）"""
    patient = db.query(models.Patient).filter(models.Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="患者不存在")

    today_count = db.query(models.Registration).filter(
        models.Registration.department == payload.department
    ).count()

    queue_number = f"{payload.department[:1].upper()}{today_count + 1:02d}"

    reg = models.Registration(
        patient_id=payload.patient_id,
        department=payload.department,
        queue_number=queue_number,
        current_state="WAITING",
        triage_symptoms=payload.triage_symptoms,
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)

    return {
        "registration_id": reg.id,
        "patient_id": reg.patient_id,
        "department": reg.department,
        "queue_number": reg.queue_number,
        "current_state": reg.current_state,
    }


@app.post("/api/agent/register/chat")
def registration_agent_chat(payload: RegistrationAgentChatRequest, db: Session = Depends(get_db)):
    """挂号阶段Agent对话接口（MVP）"""
    patient = db.query(models.Patient).filter(models.Patient.id == payload.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="患者不存在")

    session = get_or_create_agent_session(payload.session_id, payload.patient_id)
    if session["patient_id"] != payload.patient_id:
        raise HTTPException(status_code=400, detail="session_id 与 patient_id 不匹配")

    text = payload.message.strip()
    lower_text = text.lower()

    detected_dept = detect_department(text)
    detected_date = detect_date(text)
    if detected_dept:
        session["slots"]["department"] = detected_dept
    if detected_date:
        session["slots"]["date"] = detected_date

    if text:
        session["slots"]["triage_symptoms"] = text

    if session["state"] == RegistrationAgentState.INIT.value:
        session["state"] = RegistrationAgentState.COLLECT_INFO.value
        if not session["slots"]["department"]:
            return make_agent_response(
                "你好，我来帮你挂号。请告诉我想挂哪个科室，例如：耳鼻喉科、骨科。",
                session,
                required_fields=["department"],
                options=DEPARTMENTS,
                next_action="collect_department",
            )

    if session["state"] in [RegistrationAgentState.COLLECT_INFO.value, RegistrationAgentState.QUERY_SLOTS.value]:
        if not session["slots"]["department"]:
            session["state"] = RegistrationAgentState.COLLECT_INFO.value
            return make_agent_response(
                "我还不知道你想挂哪个科室，请补充科室信息。",
                session,
                required_fields=["department"],
                options=DEPARTMENTS,
                next_action="collect_department",
            )

        if not session["slots"]["date"]:
            session["slots"]["date"] = datetime.now().strftime("%Y-%m-%d")

        session["state"] = RegistrationAgentState.QUERY_SLOTS.value
        doctors = db.query(models.Doctor).filter(
            models.Doctor.department == session["slots"]["department"],
            models.Doctor.is_available == 1
        ).all()

        options = [
            {
                "doctor_id": d.id,
                "doctor_name": d.name,
                "title": d.title,
                "department": d.department,
            }
            for d in doctors
        ]
        session["last_options"] = options

        session["state"] = RegistrationAgentState.CONFIRM.value
        return make_agent_response(
            f"已为你查询到 {session['slots']['department']}（{session['slots']['date']}）可预约医生。确认挂号请回复：确认。",
            session,
            required_fields=[],
            options=options,
            next_action="confirm_registration",
        )

    if session["state"] == RegistrationAgentState.CONFIRM.value:
        if "确认" in text or lower_text in ["ok", "yes", "y"]:
            session["state"] = RegistrationAgentState.SUBMIT.value

            today_count = db.query(models.Registration).filter(
                models.Registration.department == session["slots"]["department"]
            ).count()
            queue_number = f"{session['slots']['department'][:1].upper()}{today_count + 1:02d}"

            reg = models.Registration(
                patient_id=payload.patient_id,
                department=session["slots"]["department"],
                queue_number=queue_number,
                current_state="WAITING",
                triage_symptoms=session["slots"]["triage_symptoms"],
            )
            db.add(reg)
            db.commit()
            db.refresh(reg)

            session["state"] = RegistrationAgentState.SUCCESS.value
            return make_agent_response(
                f"挂号成功，科室：{reg.department}，排队号：{reg.queue_number}。当前状态：候诊中。",
                session,
                required_fields=[],
                options=[{"registration_id": reg.id, "queue_number": reg.queue_number}],
                next_action="go_waiting",
            )

        return make_agent_response(
            "如果信息无误，请回复“确认”完成挂号；如果要改科室，请直接说新的科室名称。",
            session,
            required_fields=["confirmation"],
            options=session.get("last_options", []),
            next_action="wait_user_confirmation",
        )

    if session["state"] == RegistrationAgentState.SUCCESS.value:
        return make_agent_response(
            "你已经挂号成功。如需重新挂号，请使用新的 session_id 开始新会话。",
            session,
            required_fields=[],
            options=[],
            next_action="done",
        )

    session["state"] = RegistrationAgentState.FAILED.value
    return make_agent_response(
        "抱歉，当前流程异常，请重新开始挂号。",
        session,
        required_fields=[],
        options=[],
        next_action="restart",
    )