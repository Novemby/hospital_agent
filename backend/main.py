from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import desc
from backend.database import SessionLocal, engine
from backend import models
from pydantic import BaseModel


# --- 定义接收前端 JSON 数据的格式 ---
class DoctorLoginRequest(BaseModel):
    work_no: str
    password: str


# 自动在数据库中创建表（如果表已存在则忽略）
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="无障碍智慧医疗辅助系统 API")

@app.on_event("startup")
def log_app_info():
    print("[STARTUP_DEBUG] main.py:", __file__)
    print("[STARTUP_DEBUG] routes:", [route.path + " " + ",".join(sorted(route.methods)) for route in app.routes])

# 配置 CORS，允许前端 Vue 项目跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段允许所有地址，生产环境应严格限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 依赖注入：获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==========================================
# API 路由区域
# ==========================================

@app.get("/api/patients")
def get_patients(dept: str = Query(None, description="科室过滤，可选"), db: Session = Depends(get_db)):
    """
    获取所有患者信息（支持按科室过滤）
    返回的每个患者包含最新挂号科室（dept），若无挂号记录则 dept 为 None
    """
    # 先查询所有患者
    patients = db.query(models.Patient).all()
    result = []

    for patient in patients:
        # 获取该患者的最新挂号记录（按创建时间倒序）
        latest_reg = db.query(models.Registration).filter(
            models.Registration.patient_id == patient.id
        ).order_by(desc(models.Registration.created_at)).first()

        # 若提供了科室参数，则只返回科室匹配的患者
        if dept and (not latest_reg or latest_reg.department != dept):
            continue

        # 构造返回数据
        result.append({
            "id": patient.id,
            "name": patient.name,
            "gender": patient.gender,
            "birth_date": patient.birth_date,
            "contact_phone": patient.contact_phone,
            "id_card": patient.id_card,
            "allergy_history": patient.allergy_history,
            "is_deaf": patient.is_deaf,
            "dept": latest_reg.department if latest_reg else None   # 最新挂号科室
        })

    return result


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
    registrations = db.query(models.Registration).filter(
        models.Registration.patient_id == patient_id
    ).all()
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


# ==========================================
# 登录专属 API 路由
# ==========================================

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

    print("[LOGIN_DEBUG] work_no:", login_data.work_no)
    print("[LOGIN_DEBUG] input_password:", login_data.password)
    print("[LOGIN_DEBUG] doctor_record:", doctor.work_no if doctor else None, doctor.password if doctor else None)

    if not doctor or doctor.password != login_data.password:
        raise HTTPException(status_code=401, detail="工号或密码错误")

    return {
        "id": doctor.id,
        "work_no": doctor.work_no,
        "name": doctor.name,
        "department": doctor.department,
        "title": doctor.title
    }