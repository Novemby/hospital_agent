from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, crud, schemas
from backend.utils import helpers
from backend.config import settings

router = APIRouter(prefix="/api/agent/register", tags=["agent"])


@router.post("/chat")
def registration_agent_chat(
    payload: schemas.RegistrationAgentChatRequest,
    db: Session = Depends(get_db),
):
    # 验证患者是否存在
    patient = crud.get_patient_by_id(db, payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="患者不存在")

    # 获取或创建会话
    session = helpers.get_or_create_agent_session(payload.session_id, payload.patient_id)
    if session["patient_id"] != payload.patient_id:
        raise HTTPException(status_code=400, detail="session_id 与 patient_id 不匹配")

    text = payload.message.strip()
    lower_text = text.lower()

    # 尝试从消息中提取科室和日期
    detected_dept = helpers.detect_department(text)
    detected_date = helpers.detect_date(text)
    if detected_dept:
        session["slots"]["department"] = detected_dept
    if detected_date:
        session["slots"]["date"] = detected_date
    if text:
        session["slots"]["triage_symptoms"] = text

    # ========== 状态机处理 ==========
    # 状态: INIT
    if session["state"] == "INIT":
        session["state"] = "COLLECT_INFO"
        if not session["slots"]["department"]:
            return helpers.make_agent_response(
                "你好，我来帮你挂号。请告诉我想挂哪个科室，例如：耳鼻喉科、骨科。",
                session,
                required_fields=["department"],
                options=settings.DEPARTMENTS,
                next_action="collect_department",
            )

    # 状态: COLLECT_INFO 或 QUERY_SLOTS
    if session["state"] in ["COLLECT_INFO", "QUERY_SLOTS"]:
        # 缺少科室信息
        if not session["slots"]["department"]:
            session["state"] = "COLLECT_INFO"
            return helpers.make_agent_response(
                "我还不知道你想挂哪个科室，请补充科室信息。",
                session,
                required_fields=["department"],
                options=settings.DEPARTMENTS,
                next_action="collect_department",
            )

        # 如果还没有日期，默认今天
        if not session["slots"]["date"]:
            session["slots"]["date"] = datetime.now().strftime("%Y-%m-%d")

        session["state"] = "QUERY_SLOTS"
        # 获取科室下的医生列表（用于展示）
        doctors = db.query(models.Doctor).filter(
            models.Doctor.department == session["slots"]["department"],
            models.Doctor.is_available == 1,
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

        session["state"] = "CONFIRM"
        return helpers.make_agent_response(
            f"已为你查询到 {session['slots']['department']}（{session['slots']['date']}）可预约医生。确认挂号请回复：确认。",
            session,
            required_fields=[],
            options=options,
            next_action="confirm_registration",
        )

    # 状态: CONFIRM
    if session["state"] == "CONFIRM":
        if "确认" in text or lower_text in ["ok", "yes", "y"]:
            session["state"] = "SUBMIT"
            try:
                reg = crud.create_registration(
                    db,
                    patient_id=payload.patient_id,
                    department=session["slots"]["department"],
                    schedule_id=None,
                    triage_symptoms=session["slots"]["triage_symptoms"],
                )
            except Exception:
                db.rollback()
                raise

            session["state"] = "SUCCESS"
            return helpers.make_agent_response(
                f"挂号成功，科室：{reg.department}，排队号：{reg.queue_number}。当前状态：候诊中。",
                session,
                required_fields=[],
                options=[{"registration_id": reg.id, "queue_number": reg.queue_number}],
                next_action="go_waiting",
            )

        return helpers.make_agent_response(
            "如果信息无误，请回复“确认”完成挂号；如果要改科室，请直接说新的科室名称。",
            session,
            required_fields=["confirmation"],
            options=session.get("last_options", []),
            next_action="wait_user_confirmation",
        )

    # 状态: SUCCESS
    if session["state"] == "SUCCESS":
        return helpers.make_agent_response(
            "你已经挂号成功。如需重新挂号，请使用新的 session_id 开始新会话。",
            session,
            required_fields=[],
            options=[],
            next_action="done",
        )

    # 其他情况：失败状态
    session["state"] = "FAILED"
    return helpers.make_agent_response(
        "抱歉，当前流程异常，请重新开始挂号。",
        session,
        required_fields=[],
        options=[],
        next_action="restart",
    )