from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from .. import crud, schemas


router = APIRouter(prefix="/api/doctors", tags=["doctors"])

@router.post("/login", response_model=schemas.DoctorOut)
def doctor_login(login_data: schemas.DoctorLoginRequest, db: Session = Depends(get_db)):
    doctor = crud.get_doctor_by_workno(db, login_data.work_no)
    if not doctor or doctor.password != login_data.password:
        raise HTTPException(status_code=401, detail="工号或密码错误")
    return doctor

@router.get("/{doctor_id}", response_model=schemas.DoctorOut)
def get_doctor(doctor_id: int, db: Session = Depends(get_db)):
    doctor = crud.get_doctor_by_id(db, doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="未找到该医生信息")
    return doctor