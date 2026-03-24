from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from ..database import get_db
from .. import crud, schemas
from ..config import settings

router = APIRouter(prefix="/api/registration", tags=["registration"])

@router.get("/departments")
def get_registration_departments(db: Session = Depends(get_db)):
    db_departments = crud.get_active_departments(db)
    if db_departments:
        return {
            "departments": [d.dept_name for d in db_departments],
            "items": [
                {"id": d.id, "dept_code": d.dept_code, "dept_name": d.dept_name}
                for d in db_departments
            ]
        }
    return {
        "departments": settings.DEPARTMENTS,
        "items": [
            {"id": None, "dept_code": settings.DEPARTMENT_CODE_MAP.get(name, "OT"), "dept_name": name}
            for name in settings.DEPARTMENTS
        ]
    }

@router.get("/slots", response_model=schemas.SlotsResponse)
def get_registration_slots(
    department: str = Query(...),
    date: str = Query(...),
    db: Session = Depends(get_db)
):
    dept_id, slots = crud.get_slots(db, department, date)
    return schemas.SlotsResponse(
        department=department,
        department_id=dept_id,
        date=date,
        slots=slots
    )

@router.post("/submit")
def submit_registration(payload: schemas.RegistrationSubmitRequest, db: Session = Depends(get_db)):
    patient = crud.get_patient_by_id(db, payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="患者不存在")
    try:
        reg = crud.create_registration(
            db, payload.patient_id, payload.department,
            payload.schedule_id, payload.triage_symptoms
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "registration_id": reg.id,
        "patient_id": reg.patient_id,
        "department_id": reg.department_id,
        "schedule_id": reg.schedule_id,
        "department": reg.department,
        "queue_number": reg.queue_number,
        "current_state": reg.current_state,
    }