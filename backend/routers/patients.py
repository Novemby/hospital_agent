import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, Union
from backend.database import get_db
from .. import crud, schemas
from pydantic import BaseModel

router = APIRouter(prefix="/api/patients", tags=["patients"])


@router.get("/by-idcard/{id_card}", response_model=schemas.PatientLoginOut)
def get_patient_by_idcard(id_card: str, db: Session = Depends(get_db)):
    patient = crud.get_patient_by_idcard(db, id_card)
    if not patient:
        raise HTTPException(status_code=404, detail="未找到该身份证对应的患者，请先建档")
    return patient


@router.get("/", response_model=list[schemas.PatientOut])
def get_patients(dept: Optional[str] = Query(None), db: Session = Depends(get_db)):
    patient_rows = crud.get_patients_by_dept(db, dept)
    result = []
    for p, latest_reg in patient_rows:
        dept_name = latest_reg.department if latest_reg else None
        triage_struct = None
        if latest_reg and latest_reg.triage_symptoms:
            try:
                parsed = json.loads(latest_reg.triage_symptoms)
                triage_struct = parsed if isinstance(parsed, dict) else None
            except Exception:
                triage_struct = None

        result.append({
            "id": p.id,
            "name": p.name,
            "gender": p.gender,
            "birth_date": p.birth_date,
            "contact_phone": p.contact_phone,
            "id_card": p.id_card,
            "allergy_history": p.allergy_history,
            "is_deaf": p.is_deaf,
            "dept": dept_name,
            "triage_struct": triage_struct,
        })
    return result


@router.get("/{patient_id}", response_model=schemas.PatientOut)
def get_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = crud.get_patient_by_id(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="未找到该患者档案")
    gender_str = "男" if patient.gender == 1 else "女" if patient.gender == 0 else "未知"
    birth_date_str = patient.birth_date.strftime("%Y-%m-%d") if patient.birth_date else ""
    return {
        "id": patient.id,
        "name": patient.name,
        "gender": patient.gender,  # 整数
        "birth_date": patient.birth_date,
        "contact_phone": patient.contact_phone,
        "id_card": patient.id_card,
        "allergy_history": patient.allergy_history,
        "is_deaf": patient.is_deaf,
        "dept": None,
    }


@router.get("/{patient_id}/registrations", response_model=list[schemas.RegistrationOut])
def get_patient_registrations(patient_id: int, db: Session = Depends(get_db)):
    registrations = crud.get_patient_registrations(db, patient_id)
    return registrations


# =====================================================================
# 🌟 核心升级：同步医生病历与处方到 MySQL 数据库 (biz_medical_record 表)
# =====================================================================
class SyncRecordRequest(BaseModel):
    medical_history: str
    prescriptions: list
    diagnosis: Optional[str] = None
    doctor_id: Optional[Union[int, str]] = None


def _resolve_doctor_id(db: Session, doctor_id: Optional[Union[int, str]]) -> Optional[int]:
    if doctor_id is None:
        return None

    if isinstance(doctor_id, int):
        return doctor_id

    doctor_id_str = str(doctor_id).strip()
    if not doctor_id_str:
        return None

    if doctor_id_str.isdigit():
        return int(doctor_id_str)

    doctor = crud.get_doctor_by_workno(db, doctor_id_str)
    return doctor.id if doctor else None


@router.post("/{patient_id}/sync_record")
def sync_patient_record(patient_id: int, req: SyncRecordRequest, db: Session = Depends(get_db)):
    # 1. 查找该患者最近的一次挂号记录
    reg = db.execute(
        text("SELECT id FROM biz_registration WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
        {"pid": patient_id}
    ).fetchone()

    if not reg:
        return {"success": False, "message": "未找到挂号记录"}

    reg_id = reg[0]
    # 将处方数组转为 JSON 字符串存入 treatment_plan 字段
    plan_json = json.dumps(req.prescriptions, ensure_ascii=False)

    # 2. 检查本次挂号是否已经存在病历记录
    existing = db.execute(
        text("SELECT id FROM biz_medical_record WHERE registration_id = :rid"),
        {"rid": reg_id}
    ).fetchone()

    resolved_doctor_id = _resolve_doctor_id(db, req.doctor_id)

    try:
        if existing:
            # 存在则更新病历和处方
            db.execute(
                text("""UPDATE biz_medical_record
                        SET chief_complaint = :mc,
                            diagnosis = :diagnosis,
                            treatment_plan = :plan,
                            doctor_id = COALESCE(:doctor_id, doctor_id)
                        WHERE id = :id"""),
                {
                    "mc": req.medical_history,
                    "diagnosis": req.diagnosis or "待确诊",
                    "plan": plan_json,
                    "doctor_id": resolved_doctor_id,
                    "id": existing[0]
                }
            )
        else:
            doctor_id = resolved_doctor_id or 1
            db.execute(
                text("""INSERT INTO biz_medical_record 
                        (registration_id, doctor_id, chief_complaint, diagnosis, treatment_plan) 
                        VALUES (:rid, :doctor_id, :mc, :diagnosis, :plan)"""),
                {
                    "rid": reg_id,
                    "doctor_id": doctor_id,
                    "mc": req.medical_history,
                    "diagnosis": req.diagnosis or "待确诊",
                    "plan": plan_json
                }
            )
        db.commit()
        print(f"✅ 成功将患者 {patient_id} 的病历和处方落盘到 MySQL")
        return {"success": True}
    except Exception as e:
        db.rollback()
        print(f"❌ 病历落盘失败: {e}")
        return {"success": False, "message": str(e)}


# =====================================================================
# 🌟 新增：从数据库拉取患者的真实历史病历记录 (联表查询)
# =====================================================================
@router.get("/{patient_id}/records")
def get_patient_records(patient_id: int, db: Session = Depends(get_db)):
    # 联表查询：挂号流水表 + 病历表 + 医生表
    query = text("""
        SELECT 
            r.created_at as visit_date,
            r.department,
            d.name as doctor_name,
            m.chief_complaint,
            m.diagnosis,
            m.treatment_plan
        FROM biz_registration r
        JOIN biz_medical_record m ON r.id = m.registration_id
        LEFT JOIN sys_doctor d ON m.doctor_id = d.id
        WHERE r.patient_id = :pid
        ORDER BY r.created_at DESC
    """)
    records = db.execute(query, {"pid": patient_id}).fetchall()

    result = []
    for row in records:
        # 解析处方的 JSON 数组
        plan_list = []
        if row.treatment_plan:
            try:
                plan_list = json.loads(row.treatment_plan)
            except Exception:
                pass

        result.append({
            "visit_date": row.visit_date.strftime("%Y-%m-%d %H:%M") if row.visit_date else "",
            "department": row.department,
            "doctor_name": row.doctor_name or "主治医师",
            "chief_complaint": row.chief_complaint,
            "diagnosis": row.diagnosis,
            "treatment_plan": plan_list
        })

    return result
