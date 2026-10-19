import json
import logging
import re
import copy
import threading
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
logger = logging.getLogger("uvicorn.error")
print("✅ agent.py loaded with print-based registration traces", flush=True)

DURATION_OPTIONS = ["今天刚开始", "1-3天", "超过1周", "很久了"]
NEGATION_PREFIXES = ["没有", "并无", "不是", "不", "没", "无"]
SYMPTOM_KEYWORDS = [
    "头晕", "头痛", "头疼", "偏头", "发烧", "发热", "咳嗽", "感冒", "流涕", "乏力",
    "恶心", "呕吐", "腹泻", "胸闷", "胸痛", "心悸", "心跳", "酸痛", "疼痛", "不适",
    "痒", "红斑", "过敏", "鼻塞", "打喷嚏", "喉咙痛", "咽痛", "嗓子痛", "气短", "胸口闷",
    "呼吸困难", "发冷", "怕冷", "咳痰", "咽干", "流鼻涕", "肚子痛", "腹痛", "胃痛",
    "拉肚子", "反酸", "烧心",
]
CORRECTION_MARKERS = ["前面说错了", "刚刚说错了", "不是", "而是", "改成", "更正", "纠正一下"]
SYMPTOM_CANONICAL_MAP = {
    "肚子痛": "腹痛",
    "胃痛": "腹痛",
    "拉肚子": "腹泻",
    "流鼻涕": "流涕",
    "喉咙痛": "咽痛",
    "嗓子痛": "咽痛",
    "胸口闷": "胸闷",
    "发烧": "发热",
    "头疼": "头痛",
}
SYMPTOM_FOLLOWUP_RULES = [
    {
        "keywords": ["咳嗽", "咳痰", "发烧", "发热", "流涕", "流鼻涕", "鼻塞", "打喷嚏", "咽痛", "喉咙痛", "嗓子痛"],
        "prefix": "明白，已记录到呼吸道相关不适。",
        "question": "这些症状大概持续多久了？",
        "options": ["今天刚开始", "1-3天", "超过1周", "很久了"],
    },
    {
        "keywords": ["腹痛", "肚子痛", "胃痛", "腹泻", "拉肚子", "恶心", "呕吐", "反酸", "烧心"],
        "prefix": "好的，已记录到消化道相关不适。",
        "question": "这些情况持续多久了？",
        "options": ["今天刚开始", "1-3天", "超过1周", "反复发作很久了"],
    },
    {
        "department": "泌尿外科",
        "keywords": ["尿血", "血尿", "尿痛", "尿频", "尿急", "排尿困难", "腰痛", "肾区痛"],
        "prefix": "好的，已记录到泌尿系统相关不适。",
        "question": "这些情况持续多久了？如果方便，也可以后续继续补充是否伴随尿痛、尿频等表现。",
        "options": ["今天刚开始", "1-3天", "超过1周", "反复发作很久了"],
    },
    {
        "keywords": ["胸闷", "胸痛", "胸口闷", "心悸", "心跳", "气短", "呼吸困难"],
        "prefix": "好的，已记录到胸部或呼吸相关不适。",
        "question": "这种情况持续多久了？如果是刚刚突然出现，也可以直接选“今天刚开始”。",
        "options": ["今天刚开始", "1-3天", "超过1周", "反复发作很久了"],
    },
    {
        "keywords": ["头晕", "头痛", "头疼", "偏头", "乏力", "怕冷", "发冷"],
        "prefix": "好的，已记录到全身或头部相关不适。",
        "question": "这种情况持续多久了？",
        "options": ["今天刚开始", "1-3天", "超过1周", "很久了"],
    },
    {
        "keywords": ["疼痛", "酸痛"],
        "prefix": "好的，已记录到疼痛相关不适。",
        "question": "疼痛大概持续多久了？",
        "options": ["今天刚开始", "1-3天", "超过1周", "很久了"],
    },
]

SIGN_TOKEN_CANONICAL_MAP = {
    "头（脑）": "头",
    "他（她、它）": "他",
    "医生（大夫）": "医生",
    "反复（重复）": "反复",
    "肚子": "肚",
    "胃部": "胃",
    "喉咙": "咽",
}


# ===========================================================================
# 🌟 内部辅助函数：获取患者长期的 JSON 结构化画像 (新增)
# ===========================================================================
def _get_patient_json_profile(db: Session, patient_id: int, mode: str = "triage") -> str:
    # 先拿死数据（性别、过敏等硬性要求）
    patient = crud.get_patient_by_id(db, patient_id)
    base_info = f"患者基础信息 - 姓名:{patient.name}, 过敏史:{patient.allergy_history}" if patient else ""

    # 获取最新的 JSON 画像
    mem_record = db.execute(
        text("SELECT ai_summary FROM ai_agent_memory WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
        {"pid": patient_id}
    ).fetchone()

    raw_memory = mem_record[0] if mem_record and str(mem_record[0]).startswith("{") else "{}"
    normalized_memory = unified_medical_agent.normalize_memory_profile(raw_memory)
    if patient and patient.allergy_history and patient.allergy_history.strip() and patient.allergy_history.strip() != "无":
        allergy_items = []
        for item in normalized_memory.get("facts", {}).get("allergies", []) or []:
            if isinstance(item, dict):
                value = str(item.get("value") or "").strip()
                if value:
                    allergy_items.append(item)
            else:
                value = str(item or "").strip()
                if value:
                    allergy_items.append({
                        "value": value,
                        "source": "legacy_migrated",
                        "confirmed": True,
                        "confidence": 0.9,
                    })

        normalized_memory["facts"]["allergies"] = allergy_items
        allergy_value = patient.allergy_history.strip()
        existing_values = {
            str(item.get("value") or "").strip()
            for item in normalized_memory["facts"]["allergies"]
            if isinstance(item, dict)
        }
        if allergy_value not in existing_values:
            normalized_memory["facts"]["allergies"].append({
                "value": allergy_value,
                "source": "system_imported",
                "confirmed": True,
                "confidence": 0.98,
            })

    if mode == "consultation":
        memory_text = unified_medical_agent.build_consultation_memory_profile(
            json.dumps(normalized_memory, ensure_ascii=False)
        )
    else:
        memory_text = unified_medical_agent.build_triage_memory_profile(
            json.dumps(normalized_memory, ensure_ascii=False)
        )
    return f"{base_info}\n【动态长期记忆】:\n{memory_text}"


@router.get("/memory/profile/{patient_id}")
def get_patient_memory_profile(patient_id: int, mode: str = "consultation", db: Session = Depends(get_db)):
    patient = crud.get_patient_by_id(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="患者不存在")

    mem_record = db.execute(
        text("SELECT ai_summary FROM ai_agent_memory WHERE patient_id = :pid ORDER BY created_at DESC LIMIT 1"),
        {"pid": patient_id}
    ).fetchone()
    raw_memory = mem_record[0] if mem_record and str(mem_record[0]).startswith("{") else "{}"
    normalized_memory = unified_medical_agent.normalize_memory_profile(raw_memory)

    if patient.allergy_history and patient.allergy_history.strip() and patient.allergy_history.strip() != "无":
        allergy_value = patient.allergy_history.strip()
        existing_values = {
            str(item.get("value") or "").strip()
            for item in normalized_memory.get("facts", {}).get("allergies", []) or []
            if isinstance(item, dict)
        }
        if allergy_value not in existing_values:
            normalized_memory.setdefault("facts", {}).setdefault("allergies", []).append({
                "value": allergy_value,
                "source": "system_imported",
                "confirmed": True,
                "confidence": 0.98,
            })

    selected_mode = "consultation" if mode == "consultation" else "triage"
    profile_text = (
        unified_medical_agent.build_consultation_memory_profile(json.dumps(normalized_memory, ensure_ascii=False))
        if selected_mode == "consultation"
        else unified_medical_agent.build_triage_memory_profile(json.dumps(normalized_memory, ensure_ascii=False))
    )
    return {
        "patient_id": patient_id,
        "mode": selected_mode,
        "normalized_memory": normalized_memory,
        "prompt_profile": json.loads(profile_text),
    }


# 保留极简的兜底时段识别
def _detect_period(text: str) -> Optional[str]:
    t = text.strip().lower()
    if "上午" in t or t in ["am", "早上", "早晨"]:
        return "上午"
    if "下午" in t or t in ["pm", "晚上", "傍晚"]:
        return "下午"
    return None


def _normalize_sign_like_input(raw_text: str) -> str:
    """
    对手语识别常见词形做轻量归一化，并补全可推断的症状组合词，
    避免「头（脑） 痛」这类输入无法命中症状抽取规则。
    """
    text = str(raw_text or "").strip()
    if not text:
        return ""

    cleaned = text.replace("（", "(").replace("）", ")")
    cleaned = re.sub(r"[，。！？；、,.!?;]", " ", cleaned)
    tokens = [token.strip() for token in cleaned.split() if token.strip()]

    normalized_tokens = []
    for token in tokens:
        bracket_match = re.match(r"^(.+?)[(].*[)]$", token)
        if bracket_match:
            token = bracket_match.group(1).strip()
        token = SIGN_TOKEN_CANONICAL_MAP.get(token, token)
        normalized_tokens.append(token)

    token_set = set(normalized_tokens)
    inferred_symptoms = []
    if {"头", "痛"}.issubset(token_set):
        inferred_symptoms.append("头痛")
    if {"头", "晕"}.issubset(token_set):
        inferred_symptoms.append("头晕")
    if {"胸", "闷"}.issubset(token_set):
        inferred_symptoms.append("胸闷")
    if {"胸", "痛"}.issubset(token_set):
        inferred_symptoms.append("胸痛")
    if {"咽", "痛"}.issubset(token_set):
        inferred_symptoms.append("咽痛")
    if ({"肚", "痛"}.issubset(token_set) or {"腹", "痛"}.issubset(token_set) or {"胃", "痛"}.issubset(token_set)):
        inferred_symptoms.append("腹痛")
    if "热" in token_set and "发热" not in text and "发烧" not in text:
        inferred_symptoms.append("发热")
    if (
        "痛" in token_set
        and not ({"头", "胸", "肚", "腹", "胃", "咽"} & token_set)
        and "疼痛" not in text
    ):
        inferred_symptoms.append("疼痛")

    parts = [text]
    normalized_text = " ".join(normalized_tokens).strip()
    if normalized_text and normalized_text != text:
        parts.append(normalized_text)
    if inferred_symptoms:
        parts.append(" ".join(inferred_symptoms))

    merged = []
    for part in parts:
        item = str(part or "").strip()
        if item and item not in merged:
            merged.append(item)
    return " ".join(merged).strip()


def _normalize_symptom_term(symptom: str) -> str:
    text = str(symptom or "").strip()
    for prefix in NEGATION_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix):].strip()
    return SYMPTOM_CANONICAL_MAP.get(text, text)


def _symptom_variants(symptom: str) -> list:
    raw = str(symptom or "").strip()
    normalized = _normalize_symptom_term(raw)
    variants = []
    for item in [raw, normalized]:
        if item and item not in variants:
            variants.append(item)
    return variants


def _text_mentions_negated(text: str, symptom: str) -> bool:
    variants = _symptom_variants(symptom)
    if not variants:
        return False
    candidates = []
    for term in variants:
        candidates.extend([
            f"不{term}",
            f"不 {term}",
            f"没{term}",
            f"没 {term}",
            f"没有{term}",
            f"没有 {term}",
            f"无{term}",
            f"无 {term}",
            f"并无{term}",
            f"并无 {term}",
            f"不是{term}",
            f"不是 {term}",
        ])
    return any(item in text for item in candidates)


def _sanitize_accompanying_symptoms(raw_text: str, symptoms: list) -> list:
    if not symptoms:
        return []

    text = (raw_text or "").strip()
    if not text:
        return []

    cleaned = []
    for symptom in symptoms:
        symptom_text = _normalize_symptom_term(symptom)
        variants = _symptom_variants(symptom)
        if (
            symptom_text
            and any(term in text for term in variants)
            and not _text_mentions_negated(text, symptom)
        ):
            cleaned.append(symptom_text)
    return cleaned


def _sanitize_negative_symptoms(raw_text: str, symptoms: list) -> list:
    if not symptoms:
        return []

    text = (raw_text or "").strip()
    if not text:
        return []

    cleaned = []
    for symptom in symptoms:
        symptom_text = _normalize_symptom_term(symptom)
        if symptom_text and _text_mentions_negated(text, symptom):
            cleaned.append(symptom_text)
    return cleaned


def _merge_preserve_order(existing: list, incoming: list) -> list:
    merged = []
    for item in list(existing or []) + list(incoming or []):
        value = str(item or "").strip()
        if value and value not in merged:
            merged.append(value)
    return merged


def _extract_rule_based_symptoms(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        return {"positive": [], "negative": []}

    positive = []
    negative = []

    for symptom in sorted(SYMPTOM_KEYWORDS, key=len, reverse=True):
        variants = _symptom_variants(symptom)
        if not any(term in text for term in variants):
            continue
        normalized = _normalize_symptom_term(symptom)
        if _text_mentions_negated(text, symptom):
            if normalized not in negative:
                negative.append(normalized)
        elif normalized not in positive:
            positive.append(normalized)

    tokens = [
        token.strip()
        for token in text.replace("，", " ").replace("。", " ").replace(",", " ").split()
        if token.strip()
    ]
    pending_negation = False
    for token in tokens:
        if token in NEGATION_PREFIXES:
            pending_negation = True
            continue
        normalized = _normalize_symptom_term(token)
        if normalized in SYMPTOM_KEYWORDS:
            if pending_negation:
                if normalized not in negative:
                    negative.append(normalized)
                if normalized in positive:
                    positive.remove(normalized)
            elif normalized not in negative and normalized not in positive:
                positive.append(normalized)
        pending_negation = False

    positive = [item for item in positive if item not in negative]
    return {"positive": positive, "negative": negative}


def _has_correction_intent(text: str) -> bool:
    raw = str(text or "").strip()
    return any(marker in raw for marker in CORRECTION_MARKERS)


def _apply_correction_to_struct(struct: dict, raw_text: str, rule_based: dict) -> None:
    if not _has_correction_intent(raw_text):
        return

    positive = rule_based.get("positive", []) or []
    negative = rule_based.get("negative", []) or []

    if positive:
        struct["primary_symptom"] = positive[0]
        struct["accompanying_symptoms"] = positive[1:]
    else:
        struct["primary_symptom"] = None
        struct["accompanying_symptoms"] = []

    if negative:
        struct["negative_symptoms"] = _merge_preserve_order([], negative)


def _log_agent_stage(stage: str, session: dict, text: str, decision: Optional[dict] = None) -> None:
    symptom_struct = session.get("symptom_struct", {}) or {}
    log_payload = {
        "stage": stage,
        "text": text,
        "state": session.get("state"),
        "department": (session.get("slots", {}) or {}).get("department"),
        "date": (session.get("slots", {}) or {}).get("date"),
        "period": (session.get("slots", {}) or {}).get("period"),
        "primary_symptom": symptom_struct.get("primary_symptom"),
        "accompanying_symptoms": symptom_struct.get("accompanying_symptoms", []),
        "negative_symptoms": symptom_struct.get("negative_symptoms", []),
        "body_part": symptom_struct.get("body_part"),
        "duration": symptom_struct.get("duration"),
    }
    if decision is not None:
        log_payload["llm_decision"] = {
            "intent": decision.get("intent"),
            "department": decision.get("department"),
            "recommended_department": decision.get("recommended_department"),
            "symptom_struct": decision.get("symptom_struct", {}),
        }
    message = f"Registration agent trace | {json.dumps(log_payload, ensure_ascii=False)}"
    logger.info(message)
    print(message, flush=True)


def _log_rule_snapshot(stage: str, text: str, session: dict) -> None:
    symptom_struct = session.get("symptom_struct", {}) or {}
    rule_based = _extract_rule_based_symptoms(text)
    payload = {
        "stage": stage,
        "text": text,
        "rule_positive": rule_based.get("positive", []),
        "rule_negative": rule_based.get("negative", []),
        "primary_symptom": symptom_struct.get("primary_symptom"),
        "accompanying_symptoms": symptom_struct.get("accompanying_symptoms", []),
        "negative_symptoms": symptom_struct.get("negative_symptoms", []),
        "department": (session.get("slots", {}) or {}).get("department"),
    }
    message = f"Registration rule trace | {json.dumps(payload, ensure_ascii=False)}"
    logger.info(message)
    print(message, flush=True)


def _build_semantic_snapshot(text: str, decision: dict) -> dict:
    llm_symptom = decision.get("symptom_struct", {}) if isinstance(decision, dict) else {}
    llm_positive = []
    llm_negative = []
    if isinstance(llm_symptom, dict):
        llm_positive = _sanitize_accompanying_symptoms(text, llm_symptom.get("accompanying_symptoms", []))
        llm_negative = _sanitize_negative_symptoms(text, llm_symptom.get("negative_symptoms", []))

    decision_positive = _sanitize_accompanying_symptoms(text, decision.get("affirmed_symptoms", []) or [])
    decision_negative = _sanitize_negative_symptoms(text, decision.get("negated_symptoms", []) or [])
    rule_based = _extract_rule_based_symptoms(text)

    primary_candidate = None
    if isinstance(llm_symptom, dict):
        primary_candidate = _normalize_symptom_term(llm_symptom.get("primary_symptom"))

    positive = _merge_preserve_order(llm_positive, decision_positive)
    positive = _merge_preserve_order(positive, rule_based["positive"])
    negative = _merge_preserve_order(llm_negative, decision_negative)
    negative = _merge_preserve_order(negative, rule_based["negative"])
    positive = [item for item in positive if item not in negative]

    if primary_candidate and primary_candidate in negative:
        primary_candidate = None
    if primary_candidate and primary_candidate in positive:
        positive = [item for item in positive if item != primary_candidate]

    if not primary_candidate and positive:
        primary_candidate = positive[0]
        positive = positive[1:]

    return {
        "primary_symptom": primary_candidate,
        "accompanying_symptoms": positive,
        "negative_symptoms": negative,
        "body_part": (llm_symptom.get("body_part") if isinstance(llm_symptom, dict) else None),
        "duration": (llm_symptom.get("duration") if isinstance(llm_symptom, dict) else None),
        "severity": (llm_symptom.get("severity") if isinstance(llm_symptom, dict) else None),
        "correction_intent": bool(decision.get("correction_intent")) or _has_correction_intent(text),
        "department_candidates": _merge_preserve_order(
            decision.get("department_candidates", []) or [],
            [decision.get("recommended_department"), decision.get("department")],
        ),
    }


def _apply_semantic_snapshot(session_struct: dict, semantic: dict, raw_text: str) -> None:
    if semantic.get("correction_intent"):
        session_struct["primary_symptom"] = semantic.get("primary_symptom")
        session_struct["accompanying_symptoms"] = semantic.get("accompanying_symptoms", []) or []
        session_struct["negative_symptoms"] = semantic.get("negative_symptoms", []) or []
        if semantic.get("body_part") is not None:
            session_struct["body_part"] = semantic.get("body_part")
        if semantic.get("duration") is not None:
            session_struct["duration"] = semantic.get("duration")
        if semantic.get("severity") is not None:
            session_struct["severity"] = semantic.get("severity")
    else:
        if semantic.get("primary_symptom"):
            session_struct["primary_symptom"] = semantic.get("primary_symptom")
        session_struct["accompanying_symptoms"] = _merge_preserve_order(
            session_struct.get("accompanying_symptoms", []),
            semantic.get("accompanying_symptoms", []),
        )
        session_struct["negative_symptoms"] = _merge_preserve_order(
            session_struct.get("negative_symptoms", []),
            semantic.get("negative_symptoms", []),
        )
        if semantic.get("body_part"):
            session_struct["body_part"] = semantic.get("body_part")
        if semantic.get("duration"):
            session_struct["duration"] = semantic.get("duration")
        if semantic.get("severity"):
            session_struct["severity"] = semantic.get("severity")

    primary_symptom = _normalize_symptom_term(session_struct.get("primary_symptom"))
    negative_symptoms = [
        _normalize_symptom_term(item)
        for item in (session_struct.get("negative_symptoms", []) or [])
        if _normalize_symptom_term(item)
    ]
    accompanying_symptoms = [
        _normalize_symptom_term(item)
        for item in (session_struct.get("accompanying_symptoms", []) or [])
        if _normalize_symptom_term(item)
    ]
    accompanying_symptoms = [item for item in accompanying_symptoms if item not in negative_symptoms]

    if primary_symptom and (
        primary_symptom in negative_symptoms or _text_mentions_negated(raw_text, primary_symptom)
    ):
        negative_symptoms = _merge_preserve_order(negative_symptoms, [primary_symptom])
        primary_symptom = None

    if primary_symptom:
        accompanying_symptoms = [item for item in accompanying_symptoms if item != primary_symptom]
    elif accompanying_symptoms:
        primary_symptom = accompanying_symptoms[0]
        accompanying_symptoms = accompanying_symptoms[1:]
    elif session_struct.get("body_part"):
        primary_symptom = "不适"

    session_struct["primary_symptom"] = primary_symptom
    session_struct["accompanying_symptoms"] = _merge_preserve_order([], accompanying_symptoms)
    session_struct["negative_symptoms"] = _merge_preserve_order([], negative_symptoms)


def _enforce_primary_symptom_from_text(session: dict, raw_text: str) -> None:
    rule_based = _extract_rule_based_symptoms(raw_text)
    positive_symptoms = [
        _normalize_symptom_term(item)
        for item in (rule_based.get("positive", []) or [])
        if _normalize_symptom_term(item)
    ]
    negative_symptoms = {
        _normalize_symptom_term(item)
        for item in (session.get("symptom_struct", {}).get("negative_symptoms", []) or [])
        if _normalize_symptom_term(item)
    }
    positive_symptoms = [item for item in positive_symptoms if item not in negative_symptoms]
    if not positive_symptoms:
        return

    struct = session.get("symptom_struct", {})
    current_primary = _normalize_symptom_term(struct.get("primary_symptom"))
    if current_primary and current_primary not in negative_symptoms:
        return

    struct["primary_symptom"] = positive_symptoms[0]
    existing_acc = [
        _normalize_symptom_term(item)
        for item in (struct.get("accompanying_symptoms", []) or [])
        if _normalize_symptom_term(item)
    ]
    merged_acc = _merge_preserve_order(existing_acc, positive_symptoms[1:])
    struct["accompanying_symptoms"] = [item for item in merged_acc if item != struct["primary_symptom"]]


def _clear_stale_department_when_symptom_missing(session: dict, raw_text: str) -> None:
    struct = session.get("symptom_struct", {}) or {}
    if struct.get("primary_symptom"):
        return
    rule_based = _extract_rule_based_symptoms(raw_text)
    has_symptom_signal = bool((rule_based.get("positive", []) or []) or (rule_based.get("negative", []) or []))
    if has_symptom_signal:
        session["slots"]["department"] = None
        session["slots"]["schedule_id"] = None
        session["slots"]["period"] = None


def _select_department(session: dict, decision: dict) -> Optional[str]:
    current_department = session["slots"].get("department")
    if _is_change_department_intent(session.get("_last_text_for_department", ""), decision.get("intent")) and decision.get("department"):
        return decision["department"]
    if decision.get("department") and not current_department:
        return decision["department"]
    if decision.get("recommended_department"):
        return decision.get("recommended_department")
    for candidate in decision.get("department_candidates", []) or []:
        if candidate:
            return candidate

    struct = session.get("symptom_struct", {}) or {}
    symptom_text = " ".join(
        [
            struct.get('body_part', '') or '',
            struct.get('primary_symptom', '') or '',
            " ".join(struct.get('accompanying_symptoms', []) or []),
        ]
    ).strip()
    return _infer_department_from_symptom(symptom_text)


def _build_duration_followup(symptom_struct: dict, department: Optional[str] = None) -> dict:
    bp = symptom_struct.get("body_part") or ""
    ps = symptom_struct.get("primary_symptom") or "不适"
    symptom_name = ps if bp and bp in ps else f"{bp}{ps}"

    if department == "泌尿外科":
        if symptom_name == "腹部腹痛":
            symptom_name = "腹痛"
        if symptom_name == "不适":
            symptom_name = "泌尿系统不适"
    elif department == "消化内科":
        if symptom_name == "腹部腹痛":
            symptom_name = "腹痛"
    elif department == "心内科":
        if symptom_name == "胸部胸痛":
            symptom_name = "胸痛"
        elif symptom_name == "胸部胸闷":
            symptom_name = "胸闷"
    elif department == "神经内科":
        if symptom_name == "头部头痛":
            symptom_name = "头痛"
        elif symptom_name == "头部头晕":
            symptom_name = "头晕"

    acc_list = symptom_struct.get("accompanying_symptoms") or []
    neg_list = symptom_struct.get("negative_symptoms") or []
    details = [f"有「{symptom_name}」"]
    if acc_list:
        details.append(f"伴有「{'、'.join(acc_list)}」")
    if neg_list:
        details.append(f"但没有「{'、'.join(neg_list)}」")

    combined_text = " ".join([symptom_name] + acc_list)
    selected_rule = None
    if department:
        for rule in SYMPTOM_FOLLOWUP_RULES:
            if rule.get("department") == department:
                selected_rule = rule
                break
    for rule in SYMPTOM_FOLLOWUP_RULES:
        if selected_rule is not None:
            break
        if any(keyword in combined_text for keyword in rule["keywords"]):
            selected_rule = rule
            break

    if selected_rule is None:
        selected_rule = {
            "prefix": "明白，已了解您的情况。",
            "question": "为了帮医生准确评估病情，请问持续多久了？",
            "options": DURATION_OPTIONS,
        }

    reply = f"{selected_rule['prefix']}您提到{ '，'.join(details) }。{selected_rule['question']}"
    return {
        "reply": reply,
        "options": selected_rule["options"],
    }


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
    best_department = None
    best_score = 0
    for keywords, dept in mapping:
        matched_keywords = [keyword for keyword in keywords if keyword in symptom_text]
        if not matched_keywords:
            continue
        # 更长、更具体的词优先；多个命中词也会抬高置信度。
        score = sum(len(keyword) for keyword in matched_keywords)
        if score > best_score:
            best_score = score
            best_department = dept
    return best_department


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


def _registration_chat_legacy(payload: schemas.RegistrationAgentChatRequest, db: Session):
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
        "negative_symptoms": [],
        "original_text": "",
    })
    session.setdefault("policy", {
        "soft_ask_count": 0,
    })

    raw_text = payload.message.strip()
    text = _normalize_sign_like_input(raw_text)
    session["_last_text_for_department"] = text

    if "重新描述" in text:
        session["state"] = "COLLECT_INFO"
        session["slots"]["department"] = None
        session["slots"]["schedule_id"] = None
        session["slots"]["period"] = None
        session["symptom_struct"] = {
            k: ([] if k in ["accompanying_symptoms", "negative_symptoms"] else None)
            for k in session["symptom_struct"]
        }
        session["symptom_struct"]["original_text"] = ""
        session["policy"]["soft_ask_count"] = 0
        return helpers.make_agent_response("好的，请重新描述你的症状，我会重新为你推荐科室。", session, ["symptom"], [],
                                           "collect_symptom")

    # 🌟 核心升级：从数据库中提取该患者的长期的 JSON 画像
    patient_profile = _get_patient_json_profile(db, payload.patient_id, mode="triage")

    # 1. 🌟 调用同一个 Agent，执行【分诊阶段】任务，并传入长期记忆(patient_profile)
    decision = unified_medical_agent.triage_stage_understand(
        text,
        session.get("slots", {}),
        session.get("symptom_struct", {}),
        patient_profile=patient_profile
    )
    _log_agent_stage("llm_decision", session, text, decision)

    if decision.get("date"):
        session["slots"]["date"] = decision["date"]

    period = decision.get("period") or _detect_period(text)
    if period:
        session["slots"]["period"] = period

    semantic = _build_semantic_snapshot(text, decision)
    _apply_semantic_snapshot(session["symptom_struct"], semantic, text)
    _enforce_primary_symptom_from_text(session, text)
    _clear_stale_department_when_symptom_missing(session, text)
    _log_rule_snapshot("post_rule_merge", text, session)

    # 确保原话永远在追加
    if raw_text and raw_text not in str(session["symptom_struct"].get("original_text", "")):
        session["symptom_struct"]["original_text"] = (
                str(session["symptom_struct"].get("original_text", "")) + " " + raw_text).strip()

    session["slots"]["department"] = _select_department(session, decision) or session["slots"].get("department")

    # 落库前同步状态
    session["slots"]["triage_symptoms"] = json.dumps(session["symptom_struct"], ensure_ascii=False)
    _log_agent_stage("post_merge", session, text)

    lower_text = text.lower()
    is_confirm = raw_text.strip().rstrip("。！!").lower() in {"确认", "确认挂号", "确定挂号", "ok", "yes", "y"}

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
            followup = _build_duration_followup(
                session["symptom_struct"],
                session["slots"].get("department"),
            )

            return helpers.make_agent_response(
                followup["reply"],
                session,
                ["duration"],
                followup["options"],
                "collect_duration",
            )

        if missing == "body_part":
            session["state"] = "COLLECT_INFO"
            return helpers.make_agent_response("请补充具体部位，例如：左肩、右上腹、后脑勺。", session, ["body_part"], [],
                                               "collect_body_part")

    if not session["slots"].get("department"):
        inferred = _select_department(session, decision)
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


_workflow_locks = {}
_workflow_locks_guard = threading.Lock()


@router.post("/chat")
def registration_agent_chat(payload: schemas.RegistrationAgentChatRequest, db: Session = Depends(get_db)):
    if not payload.message.strip() or len(payload.message) > 2000 or not payload.session_id or len(payload.session_id) > 128:
        raise HTTPException(status_code=422, detail="请提供有效会话及 1–2000 字的问题")
    if not crud.get_patient_by_id(db, payload.patient_id):
        raise HTTPException(status_code=404, detail="患者不存在")
    try:
        from backend.services.hospital_workflow import run_workflow
    except ImportError:
        raise HTTPException(status_code=503, detail="请安装 requirements-workflow.txt 中的 LangGraph 依赖") from None
    with _workflow_locks_guard:
        lock = _workflow_locks.setdefault(payload.session_id, threading.RLock())
    with lock:
        session = helpers.get_or_create_agent_session(payload.session_id, payload.patient_id)
        if session["patient_id"] != payload.patient_id:
            raise HTTPException(status_code=400, detail="session_id 与 patient_id 不匹配")
        cache = session.setdefault("workflow_requests", {})
        if payload.request_id and payload.request_id in cache:
            stored = cache[payload.request_id]
            if stored["message"] != payload.message:
                raise HTTPException(status_code=409, detail="request_id 已用于不同问题")
            return copy.deepcopy(stored["response"])

        def registration(message):
            request = payload.model_copy(update={"message": message})
            return _registration_chat_legacy(request, db)

        def knowledge(question):
            try:
                from backend.services.rag.answers import RAGAnswerService
                return RAGAnswerService.from_env().answer(question)
            except (ImportError, ValueError, OSError, KeyError):
                return {"status": "retrieval_unavailable", "answer": "知识服务暂不可用，可以选择继续挂号。", "sources": []}

        def business_query(message, slots):
            if "排队" in message:
                return "当前编排尚未接入排队进度查询，请在就诊记录或向工作人员核实。"
            department = helpers.detect_department(message) or slots.get("department")
            date = helpers.detect_date(message) or slots.get("date")
            if not department or not date:
                return "查询号源需要科室和日期，请完整说明，例如“明天耳鼻喉科还剩几个号”。"
            _, available = crud.get_slots(db, department, date)
            return f"{department} {date} 当前号源：" + "；".join(f"{s['period']}剩余{s['remain']}个" for s in available) + "。查询不代表已挂号。"

        result = run_workflow(payload.message, session, registration, knowledge, business_query)
        if payload.request_id:
            cache[payload.request_id] = {"message": payload.message, "response": copy.deepcopy(result)}
            while len(cache) > 20:
                del cache[next(iter(cache))]
        return result


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
    patient_profile = _get_patient_json_profile(db, req.patient_id, mode="consultation")

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
    old_memory_json = mem_record[0] if mem_record and str(mem_record[0]).startswith("{") else "{}"
    old_memory_json = json.dumps(
        unified_medical_agent.normalize_memory_profile(old_memory_json),
        ensure_ascii=False,
    )

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
