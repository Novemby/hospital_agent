from typing import Optional

from sqlalchemy.orm import Session
from datetime import datetime, date
from sqlalchemy import func
from backend import models
from .config import settings
from fastapi import HTTPException

def _get_or_create_department(db: Session, department_name: str) -> models.Department:
    dept = db.query(models.Department).filter(models.Department.dept_name == department_name).first()
    if dept:
        return dept
    dept = models.Department(
        dept_code=settings.DEPARTMENT_CODE_MAP.get(department_name, "OT"),
        dept_name=department_name,
        is_active=True,
    )
    db.add(dept)
    db.flush()
    return dept

def _get_or_create_schedule(
    db: Session,
    department_id: int,
    queue_date_str: Optional[str] = None,
    schedule_id: Optional[int] = None,
) -> models.Schedule:
    if schedule_id:
        schedule = db.query(models.Schedule).filter(models.Schedule.id == schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="排班不存在")
        return schedule

    queue_date = datetime.strptime(queue_date_str, "%Y-%m-%d").date() if queue_date_str else datetime.now().date()
    schedule = db.query(models.Schedule).filter(
        models.Schedule.department_id == department_id,
        models.Schedule.schedule_date == queue_date,
        models.Schedule.time_period == "AM",
        models.Schedule.is_open == True,
    ).with_for_update().first()

    if not schedule:
        schedule = models.Schedule(
            department_id=department_id,
            doctor_id=None,
            schedule_date=queue_date,
            time_period="AM",
            capacity_total=50,
            capacity_used=0,
            is_open=True,
        )
        db.add(schedule)
        db.flush()

    if schedule.capacity_used >= schedule.capacity_total:
        raise HTTPException(status_code=400, detail="当前时段号源已满")

    schedule.capacity_used += 1
    return schedule

def _allocate_queue_number(db: Session, department: str, queue_date_str: Optional[str] = None) -> str:
    """按 科室+日期 事务安全地分配排队号"""
    queue_date = datetime.strptime(queue_date_str, "%Y-%m-%d").date() if queue_date_str else datetime.now().date()

    counter = db.query(models.QueueCounter).filter(
        models.QueueCounter.department == department,
        models.QueueCounter.queue_date == queue_date,
    ).with_for_update().first()

    if not counter:
        counter = models.QueueCounter(
            department=department,
            queue_date=queue_date,
            current_no=0,
        )
        db.add(counter)
        db.flush()

    counter.current_no += 1
    seq = counter.current_no
    dept_code = settings.DEPARTMENT_CODE_MAP.get(department, "OT")
    return f"{dept_code}{queue_date.strftime('%Y%m%d')}-{seq:03d}"

def get_patient_by_idcard(db: Session, id_card: str):
    return db.query(models.Patient).filter(models.Patient.id_card == id_card).first()

def get_patient_by_id(db: Session, patient_id: int):
    return db.query(models.Patient).filter(models.Patient.id == patient_id).first()

def get_patients_by_dept(db: Session, dept: str = None, current_state: Optional[str] = "WAITING"):
    latest_reg_subquery = db.query(
        models.Registration.patient_id.label("patient_id"),
        func.max(models.Registration.id).label("latest_registration_id"),
    ).group_by(models.Registration.patient_id).subquery()

    query = db.query(models.Patient, models.Registration).join(
        latest_reg_subquery,
        models.Patient.id == latest_reg_subquery.c.patient_id
    ).join(
        models.Registration,
        models.Registration.id == latest_reg_subquery.c.latest_registration_id
    )

    if dept:
        query = query.filter(models.Registration.department == dept)
    if current_state:
        query = query.filter(models.Registration.current_state == current_state)

    return query.order_by(models.Registration.created_at.desc(), models.Registration.id.desc()).all()

def get_doctor_by_workno(db: Session, work_no: str):
    return db.query(models.Doctor).filter(models.Doctor.work_no == work_no).first()

def get_doctor_by_id(db: Session, doctor_id: int):
    return db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()

def get_patient_registrations(db: Session, patient_id: int):
    return db.query(models.Registration).filter(
        models.Registration.patient_id == patient_id
    ).order_by(models.Registration.created_at.desc(), models.Registration.id.desc()).all()

def get_active_departments(db: Session):
    return db.query(models.Department).filter(models.Department.is_active == True).all()

def get_slots(db: Session, department: str, date_str: str):
    dept = db.query(models.Department).filter(models.Department.dept_name == department).first()
    if not dept:
        dept = _get_or_create_department(db, department)
        db.commit()
        db.refresh(dept)

    schedule_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    schedules = db.query(models.Schedule).filter(
        models.Schedule.department_id == dept.id,
        models.Schedule.schedule_date == schedule_date,
        models.Schedule.is_open == True,
    ).all()

    if not schedules:
        am_schedule = models.Schedule(
            department_id=dept.id,
            doctor_id=None,
            schedule_date=schedule_date,
            time_period="AM",
            capacity_total=50,
            capacity_used=0,
            is_open=True,
        )
        pm_schedule = models.Schedule(
            department_id=dept.id,
            doctor_id=None,
            schedule_date=schedule_date,
            time_period="PM",
            capacity_total=50,
            capacity_used=0,
            is_open=True,
        )
        db.add(am_schedule)
        db.add(pm_schedule)
        db.commit()
        schedules = [am_schedule, pm_schedule]

    doctors = db.query(models.Doctor).filter(
        models.Doctor.department == department,
        models.Doctor.is_available == 1
    ).all()
    doctor_name = doctors[0].name if doctors else "待分配"
    doctor_id = doctors[0].id if doctors else None

    slots = []
    for s in schedules:
        slots.append({
            "slot_id": s.id,
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "department": department,
            "date": date_str,
            "period": "上午" if s.time_period == "AM" else "下午",
            "remain": max(0, s.capacity_total - s.capacity_used),
            "capacity_total": s.capacity_total,
            "capacity_used": s.capacity_used,
        })
    return dept.id, slots

def create_registration(db: Session, patient_id: int, department: str, schedule_id: Optional[int],
                        triage_symptoms: Optional[str]) -> models.Registration:
    dept = _get_or_create_department(db, department)
    schedule = _get_or_create_schedule(db, dept.id, schedule_id=schedule_id)
    queue_number = _allocate_queue_number(db, department, schedule.schedule_date.strftime("%Y-%m-%d"))
    reg = models.Registration(
        patient_id=patient_id,
        department_id=dept.id,
        schedule_id=schedule.id,
        department=department,
        queue_number=queue_number,
        current_state="WAITING",
        triage_symptoms=triage_symptoms,
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return reg
