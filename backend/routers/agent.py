import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend import crud, schemas
from backend.config import settings
from backend.database import get_db
from backend.utils import helpers
from backend.utils.langchain_agent import unified_medical_agent  # 🌟 引入全局单例的统一 Agent
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api/agent/register", tags=["agent"])

DURATION_OPTIONS = ["今天刚开始", "1-3天", "超过1周", "很久了"]


# ===========================================================================
# 🌟 内部辅助函数：获取患者长期的 JSON 结构化画像 (新增)
# ===========================================================================
def _get_patient_json_profile(db: Session, patient_id: int) -> str:
    # 先拿死数据（性别、过敏等硬性要求）
    patient = crud.get_patient_by_id(db, patient_id)
    base_info = f"患者基础信息 - 姓名:{patient.name}, 过敏史:{patient.allergy_history}" if patient else ""

    # 获取最新的 JSON 画像
    mem_record = db.execute(
        text("SELECT ai_summary FROM ai_agent_memory WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
        {"pid": patient_id}
    ).fetchone()

    json_memory = mem_record[0] if mem_record and mem_record[0].startswith("{") else "{}"
    return f"{base_info}\n【动态长期记忆】:\n{json_memory}"


# 保留极简的兜底时段识别
def _detect_period(text: str) -> Optional[str]:
    t = text.strip().lower()
    if "上午" in t or t in ["am", "早上", "早晨"]:
        return "上午"
    if "下午" in t or t in ["pm", "晚上", "傍晚"]:
        return "下午"
    return None


def _sanitize_accompanying_symptoms(raw_text: str, symptoms: list) -> list:
    if not symptoms:
        return []

    text = (raw_text or "").strip()
    if not text:
        return []

    cleaned = []
    for symptom in symptoms:
        symptom_text = str(symptom).strip()
        if symptom_text and symptom_text in text:
            cleaned.append(symptom_text)
    return cleaned


def _next_missing_field(symptom: dict) -> Optional[str]:
    # 强制本地按顺序校验缺失槽位
    if not symptom.get("primary_symptom"):
        return "primary_symptom"
    if symptom.get("primary_symptom") == "疼痛" and not symptom.get("body_part"):
        return "body_part"
    if not symptom.get("duration"):
        return "duration"
    return None


def _is_urgent_registration_intent(text: str) -> bool:
    urgent_keywords = ["快点", "赶紧", "马上", "很痛", "受不了", "先挂号", "先给我挂号"]
    return any(k in text for k in urgent_keywords)


def _is_change_department_intent(text: str, intent: Optional[str] = None) -> bool:
    if intent == "change_department":
        return True
    return any(k in text for k in ["换科室", "改挂", "不要这个科", "换一个科", "重新选科室"])


def _soft_slots_complete(symptom: dict) -> bool:
    return bool(symptom.get("duration"))


def _has_core_ready(session: dict) -> bool:
    return bool(session.get("slots", {}).get("department"))


def _infer_department_from_symptom(symptom_text: str) -> Optional[str]:
    mapping = [
        (["头", "脑", "头晕", "头痛", "头疼", "偏头"], "神经内科"),
        (["心", "胸", "心跳", "心悸", "胸闷", "胸痛"], "心内科"),
        (["肚", "腹", "胃", "肠", "恶心", "呕吐", "腹泻", "消化"], "消化内科"),
        (["骨", "关节", "腰", "颈", "肩", "膝", "腿", "手", "脚", "扭伤", "骨折", "酸痛"], "骨科"),
        (["皮肤", "疹", "痒", "红斑", "过敏"], "皮肤科"),
        (["眼", "视力", "眼睛"], "眼科"),
        (["耳", "鼻", "喉", "咽", "听力"], "耳鼻喉科"),
        (["发热", "发烧", "咳嗽", "感冒", "流涕", "乏力", "不适"], "内科"),
    ]
    for keywords, dept in mapping:
        if any(k in symptom_text for k in keywords):
            return dept
    return None


def _should_proceed_to_slots(session: dict, text: str) -> bool:
    if not _has_core_ready(session):
        return False

    symptom = session.get("symptom_struct", {})
    if not symptom.get("primary_symptom"):
        return False
    if symptom.get("primary_symptom") == "疼痛" and not symptom.get("body_part"):
        return False

    if _soft_slots_complete(symptom):
        return True

    policy = session.get("policy", {})
    soft_ask_count = int(policy.get("soft_ask_count", 0))
    if soft_ask_count >= 2:
        return True
    if _is_urgent_registration_intent(text):
        return True
    return False


@router.post("/chat")
def registration_agent_chat(payload: schemas.RegistrationAgentChatRequest, db: Session = Depends(get_db)):
    patient = crud.get_patient_by_id(db, payload.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="患者不存在")

    session = helpers.get_or_create_agent_session(payload.session_id, payload.patient_id)
    if session["patient_id"] != payload.patient_id:
        raise HTTPException(status_code=400, detail="session_id 与 patient_id 不匹配")

    session.setdefault("symptom_struct", {
        "primary_symptom": None,
        "body_part": None,
        "duration": None,
        "severity": None,
        "accompanying_symptoms": [],
        "original_text": "",
    })
    session.setdefault("policy", {
        "soft_ask_count": 0,
    })

    text = payload.message.strip()

    if "重新描述" in text:
        session["state"] = "COLLECT_INFO"
        session["slots"]["department"] = None
        session["slots"]["schedule_id"] = None
        session["slots"]["period"] = None
        session["symptom_struct"] = {k: (None if k != "accompanying_symptoms" else []) for k in
                                     session["symptom_struct"]}
        session["symptom_struct"]["original_text"] = ""
        session["policy"]["soft_ask_count"] = 0
        return helpers.make_agent_response("好的，请重新描述你的症状，我会重新为你推荐科室。", session, ["symptom"], [],
                                           "collect_symptom")

    # 🌟 核心升级：从数据库中提取该患者的长期的 JSON 画像
    patient_profile = _get_patient_json_profile(db, payload.patient_id)

    # 1. 🌟 调用同一个 Agent，执行【分诊阶段】任务，并传入长期记忆(patient_profile)
    decision = unified_medical_agent.triage_stage_understand(
        text,
        session.get("slots", {}),
        session.get("symptom_struct", {}),
        patient_profile=patient_profile
    )

    # 2. 动态更新科室
    current_department = session["slots"].get("department")
    change_department = _is_change_department_intent(text, decision.get("intent"))

    if change_department and decision.get("department"):
        session["slots"]["department"] = decision["department"]
    elif decision.get("department") and not current_department:
        session["slots"]["department"] = decision["department"]
    elif decision.get("recommended_department"):
        session["slots"]["department"] = decision.get("recommended_department")

    if decision.get("date"):
        session["slots"]["date"] = decision["date"]

    period = decision.get("period") or _detect_period(text)
    if period:
        session["slots"]["period"] = period

    # ==============================================================
    # 🌟 核心修复 2：智能防覆盖机制，杜绝大模型返回的空值抹掉现有记录
    # ==============================================================
    llm_symptom = decision.get("symptom_struct", {})
    if llm_symptom and isinstance(llm_symptom, dict):
        if isinstance(llm_symptom.get("accompanying_symptoms"), list):
            llm_symptom["accompanying_symptoms"] = _sanitize_accompanying_symptoms(
                text,
                llm_symptom.get("accompanying_symptoms", [])
            )
        for k, v in llm_symptom.items():
            if v:  # 只有 LLM 真的提取到了内容才更新，拒绝 null 覆盖
                if k == "accompanying_symptoms" and isinstance(v, list):
                    existing = session["symptom_struct"].get(k, [])
                    session["symptom_struct"][k] = list(set(existing + v))
                else:
                    session["symptom_struct"][k] = v

    # 确保原话永远在追加
    if text and text not in str(session["symptom_struct"].get("original_text", "")):
        session["symptom_struct"]["original_text"] = (
                str(session["symptom_struct"].get("original_text", "")) + " " + text).strip()

    # ==============================================================
    # 🌟 核心修复 3：强行提权机制！打断一切可能的死循环！
    # ==============================================================
    struct = session["symptom_struct"]
    if not struct.get("primary_symptom"):
        # 如果大模型死板地把“头晕”放在了伴随症状里，强行把它提出来当作核心症状！
        if struct.get("accompanying_symptoms"):
            struct["primary_symptom"] = struct["accompanying_symptoms"].pop(0)
        # 如果大模型只给了一个部位（比如只提了头部），强行组装一个保底词
        elif struct.get("body_part"):
            struct["primary_symptom"] = "不适"

    # 落库前同步状态
    session["slots"]["triage_symptoms"] = json.dumps(session["symptom_struct"], ensure_ascii=False)

    lower_text = text.lower()
    is_confirm = bool(decision.get("confirm")) or ("确认" in text or lower_text in ["ok", "yes", "y"])

    # 4. 判断流程走向
    allow_query_slots = _should_proceed_to_slots(session, text)

    if not allow_query_slots and session["state"] not in ["CONFIRM", "SUBMIT", "SUCCESS"]:
        missing = _next_missing_field(session["symptom_struct"])

        if missing in ["duration"]:
            session["policy"]["soft_ask_count"] = int(session["policy"].get("soft_ask_count", 0)) + 1

        if missing == "primary_symptom":
            session["state"] = "COLLECT_INFO"
            return helpers.make_agent_response(
                f"好的，将为您留意「{session['slots'].get('department') or '相关'}」科室号源。请问您具体哪里不适？",
                session, ["primary_symptom"], [], "collect_symptom"
            )

        if missing == "duration":
            session["state"] = "COLLECT_INFO"
            bp = session['symptom_struct'].get('body_part') or ''
            ps = session['symptom_struct'].get('primary_symptom') or '不适'
            symptom_name = ps if bp in ps else f"{bp}{ps}"

            acc_list = session['symptom_struct'].get('accompanying_symptoms') or []
            acc_str = f"，且伴有「{'、'.join(acc_list)}」" if acc_list else ""
            prefix = "明白，这确实需要引起重视。" if acc_list else "了解到"

            return helpers.make_agent_response(
                f"{prefix}您有「{symptom_name}」{acc_str}的情况。为了帮医生准确评估病情，请问持续多久了？",
                session, ["duration"], DURATION_OPTIONS, "collect_duration"
            )

        if missing == "body_part":
            session["state"] = "COLLECT_INFO"
            return helpers.make_agent_response("请补充具体部位，例如：左肩、右上腹、后脑勺。", session, ["body_part"], [],
                                               "collect_body_part")

    if not session["slots"].get("department"):
        # 症状信息已完整，尝试从症状自动推断科室
        struct = session["symptom_struct"]
        inferred = decision.get("recommended_department") or decision.get("department")
        if not inferred:
            symptom_text = f"{struct.get('body_part', '')} {struct.get('primary_symptom', '')} {struct.get('original_text', '')}".strip()
            inferred = _infer_department_from_symptom(symptom_text)

        if inferred:
            session["slots"]["department"] = inferred
            allow_query_slots = True  # 重新允许进入号源查询
        else:
            session["state"] = "COLLECT_INFO"
            return helpers.make_agent_response(
                "根据您的症状，请选择就诊科室：",
                session, ["department"], settings.DEPARTMENTS, "collect_department"
            )

    # 5. 静默查询号源
    if session["state"] in ["INIT", "COLLECT_INFO", "QUERY_SLOTS"]:
        if not session["slots"].get("date"):
            session["slots"]["date"] = datetime.now().strftime("%Y-%m-%d")

        session["state"] = "QUERY_SLOTS"
        _, slots = crud.get_slots(db, session["slots"]["department"], session["slots"]["date"])
        available = [s for s in slots if s.get("remain", 0) > 0]

        if not available:
            session["state"] = "COLLECT_INFO"
            return helpers.make_agent_response(
                f"{session['slots']['department']} 在 {session['slots']['date']} 暂无号源。您可以换一天，或更换科室。",
                session, ["date_or_department"], ["今天", "明天"] + settings.DEPARTMENTS, "change_date_or_department"
            )

        preferred = session["slots"].get("period") or ("上午" if datetime.now().hour < 12 else "下午")
        matched = next((s for s in available if s.get("period") == preferred), None) or available[0]

        session["slots"]["period"] = matched["period"]
        session["slots"]["schedule_id"] = matched["slot_id"]
        session["state"] = "CONFIRM"
        return helpers.make_agent_response(
            f"已为您匹配 {session['slots']['department']}（{session['slots']['date']}，{session['slots']['period']}）的号源。确认挂号请回复：确认。",
            session, ["confirmation"], ["确认", "重新描述"], "confirm_registration"
        )

    # 6. 确认与落库
    if session["state"] == "CONFIRM":
        if is_confirm:
            session["state"] = "SUBMIT"
            reg = crud.create_registration(
                db,
                patient_id=payload.patient_id,
                department=session["slots"]["department"],
                schedule_id=session["slots"]["schedule_id"],
                triage_symptoms=session["slots"].get("triage_symptoms"),
            )
            session["state"] = "SUCCESS"
            return helpers.make_agent_response(
                f"挂号成功！科室：{reg.department}，排队号：{reg.queue_number}。大屏信息已同步至医生端。",
                session, [], [{"registration_id": reg.id, "queue_number": reg.queue_number}], "go_waiting"
            )

        return helpers.make_agent_response("信息收集完毕，回复“确认”完成挂号；若需修改可点击“重新描述”。", session,
                                           ["confirmation"], ["确认", "重新描述"], "wait_user_confirmation")

    if session["state"] == "SUCCESS":
        return helpers.make_agent_response("您已挂号成功。如需新业务，请开启新会话。", session, [], [], "done")

    session["state"] = "FAILED"
    return helpers.make_agent_response("抱歉，流程出现异常，请重新开始。", session, [], [], "restart")


# ===========================================================================
# 🌟 新增端点：供前端面诊阶段 (handsign_assist.js) 调用
# ===========================================================================
class PolishRequest(BaseModel):
    patient_id: int
    words: List[str]
    context: str


@router.post("/consultation/polish")
def polish_consultation_words(req: PolishRequest, db: Session = Depends(get_db)):
    # 🌟 获取结构化 JSON 画像
    patient_profile = _get_patient_json_profile(db, req.patient_id)

    # 🌟 传入 Agent，执行翻译润色
    agent_result = unified_medical_agent.consultation_stage_polish(req.words, req.context, patient_profile)
    return agent_result


# ===========================================================================
# 🌟 新增端点：划词术语解释 API
# ===========================================================================
class ExplainRequest(BaseModel):
    term: str
    context: str = ""


@router.post("/consultation/explain")
def explain_medical_term(req: ExplainRequest):
    explanation = unified_medical_agent.explain_medical_term(req.term, req.context)
    return {"explanation": explanation}


# ===========================================================================
# 🌟 新增端点：就诊结束时的记忆沉淀 API (Memory Consolidation)
# ===========================================================================
class ConsolidateRequest(BaseModel):
    patient_id: int
    chat_history: str


@router.post("/memory/consolidate")
def consolidate_patient_memory(req: ConsolidateRequest, db: Session = Depends(get_db)):
    if not req.chat_history.strip():
        return {"message": "无对话记录，无需沉淀"}

    # 1. 取出老记忆
    mem_record = db.execute(
        text("SELECT ai_summary FROM ai_agent_memory WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
        {"pid": req.patient_id}
    ).fetchone()
    old_memory_json = mem_record[0] if mem_record and mem_record[0].startswith("{") else "{}"

    # 2. 呼叫 Agent，融合出全新的 JSON 画像
    new_memory_json = unified_medical_agent.consolidate_memory(req.chat_history, old_memory_json)

    # 3. 将提炼的精华记忆落盘到数据库的 `ai_agent_memory` 表
    try:
        # 获取最近的一条挂号记录，防止外键约束报错
        latest_reg = db.execute(
            text("SELECT id FROM biz_registration WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
            {"pid": req.patient_id}
        ).fetchone()
        reg_id = latest_reg[0] if latest_reg else 10001

        db.execute(
            text("""
            INSERT INTO ai_agent_memory (patient_id, registration_id, session_id, ai_summary) 
            VALUES (:pid, :reg_id, :sid, :summary)
            """),
            {"pid": req.patient_id, "reg_id": reg_id, "sid": f"session_{req.patient_id}", "summary": new_memory_json}
        )
        db.commit()
        print(f"✅ 结构化记忆已更新并落盘!")
    except Exception as e:
        db.rollback()
        print(f"❌ 记忆落盘失败: {e}")

    return {"message": "记忆已沉淀", "summary": new_memory_json}
