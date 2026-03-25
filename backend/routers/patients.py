from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from backend.database import get_db
from .. import crud, schemas

router = APIRouter(prefix="/api/patients", tags=["patients"])

@router.get("/by-idcard/{id_card}", response_model=schemas.PatientLoginOut)
def get_patient_by_idcard(id_card: str, db: Session = Depends(get_db)):
    patient = crud.get_patient_by_idcard(db, id_card)
    if not patient:
        raise HTTPException(status_code=404, detail="未找到该身份证对应的患者，请先建档")
    return patient

@router.get("/", response_model=list[schemas.PatientOut])
def get_patients(dept: Optional[str] = Query(None), db: Session = Depends(get_db)):
    patients = crud.get_patients_by_dept(db, dept)
    result = []
    for p in patients:
        latest_reg = crud.get_patient_registrations(db, p.id)
        dept_name = latest_reg[0].department if latest_reg else None
        # 删除 gender_str 和 birth_date_str，直接使用 p.gender 和 p.birth_date
        result.append({
            "id": p.id,
            "name": p.name,
            "gender": p.gender,  # 整数 0/1 等
            "birth_date": p.birth_date,  # date 对象或 None
            "contact_phone": p.contact_phone,
            "id_card": p.id_card,
            "allergy_history": p.allergy_history,
            "is_deaf": p.is_deaf,
            "dept": dept_name,
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