import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..config import settings
from .helpers import detect_date, detect_department

try:
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import PromptTemplate
    from langchain_openai import ChatOpenAI
except Exception:
    JsonOutputParser = None
    PromptTemplate = None
    ChatOpenAI = None


class RegistrationNLU(BaseModel):
    intent: str = Field(default="register", description="register/change_department/confirm/unknown")
    department: Optional[str] = Field(default=None,
                                      description="科室名称，必须严格来自候选列表。若用户只说症状，请推理科室。")
    date: Optional[str] = Field(default=None, description="格式 YYYY-MM-DD")
    confirm: bool = Field(default=False, description="用户是否明确确认挂号")
    triage_symptoms: Optional[str] = Field(default=None, description="症状或主诉")


class SymptomStruct(BaseModel):
    # 🌟 修复点 1：加重 Prompt 语气，严禁大模型留空主要症状
    primary_symptom: Optional[str] = Field(default=None, description="最主要的不适感受（如'头晕','头痛','发热','疼痛'等）。只要患者表达了不适，此项绝不能为null！")
    body_part: Optional[str] = Field(default=None, description="具体身体部位及方位，如'左肩', '右上腹'")
    duration: Optional[str] = Field(default=None, description="持续时间，如'1天内', '1周', '几个月'")
    severity: Optional[str] = Field(default=None, description="严重程度描述，如'轻微', '剧烈'")
    accompanying_symptoms: List[str] = Field(default_factory=list,
                                             description="伴随的其他症状列表")
    original_text: Optional[str] = Field(default=None, description="将用户本次和历史输入的关于症状的原话完整拼接保留")


class HybridAgentDecision(BaseModel):
    intent: str = Field(default="register")
    department: Optional[str] = None
    date: Optional[str] = None
    period: Optional[str] = None
    confirm: bool = False
    recommended_department: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)
    strategy: str = Field(default="collect_missing_info")
    triage_symptoms: Optional[str] = None
    symptom_struct: SymptomStruct = Field(default_factory=SymptomStruct)


def _get_llm() -> Optional["ChatOpenAI"]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not ChatOpenAI:
        return None

    model_name = os.getenv("LC_MODEL", "deepseek-chat")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    return ChatOpenAI(
        model=model_name,
        temperature=0.1,  # 降低温度，保证信息提取的稳定性
        api_key=api_key,
        base_url=base_url,
    )


def _fallback_analyze(text: str) -> Dict[str, Any]:
    lower_text = text.lower()
    return {
        "intent": "confirm" if ("确认" in text or lower_text in ["ok", "yes", "y"]) else "register",
        "department": detect_department(text),
        "date": detect_date(text),
        "confirm": ("确认" in text or lower_text in ["ok", "yes", "y"]),
        "triage_symptoms": text if text else None,
    }


def analyze_registration_message(text: str, known_slots: Dict[str, Any]) -> Dict[str, Any]:
    return _fallback_analyze(text)


def hybrid_understand_message(
        text: str,
        known_slots: Dict[str, Any],
        known_symptom: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fallback = {
        "intent": "register",
        "department": detect_department(text),
        "date": detect_date(text),
        "period": None,
        "confirm": ("确认" in text or text.lower() in ["ok", "yes", "y"]),
        "recommended_department": detect_department(text),
        "missing_fields": [],
        "strategy": "collect_missing_info",
        "triage_symptoms": text,
        "symptom_struct": {
            "primary_symptom": None,
            "body_part": None,
            "duration": None,
            "severity": None,
            "accompanying_symptoms": [],
            "original_text": text,
        },
    }

    if not text or not JsonOutputParser or not PromptTemplate:
        return fallback

    llm = _get_llm()
    if not llm:
        return fallback

    parser = JsonOutputParser(pydantic_object=HybridAgentDecision)
    prompt = PromptTemplate(
        template=(
            "你是三甲医院的专业挂号 Hybrid Agent 的核心理解模块。\n"
            "今天日期: {today}\n"
            "可用科室白名单: {departments}\n"
            "当前已知槽位: {known_slots}\n"
            "当前已知症状结构: {known_symptom}\n"
            "用户最新输入: {text}\n"
            "【核心任务】\n"
            "1. 意图与科室：推断用户的 intent。如果用户只描述了症状，必须利用医学常识推荐最合适的 recommended_department（必须在白名单内）。\n"
            "2. 症状提取 (symptom_struct)：这是重中之重！结合已知症状结构和用户最新输入，精准提取并更新 symptom_struct 中的所有字段。如果用户表达了身体不适，primary_symptom 绝对不能为null！必须保留原话(original_text)。\n"
            "3. 策略判断：判断当前缺失哪些关键信息，并决定下一步 strategy。\n"
            "【严禁事项】\n"
            "绝对不允许凭空捏造用户没有提到的症状或时间！不知道的字段留空 (null)。\n"
            "只输出合法的 JSON。\n"
            "{format_instructions}"
        ),
        input_variables=["today", "departments", "known_slots", "known_symptom", "text"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )

    try:
        result = (prompt | llm | parser).invoke(
            {
                "today": datetime.now().strftime("%Y-%m-%d"),
                "departments": "、".join(settings.DEPARTMENTS),
                "known_slots": str(known_slots or {}),
                "known_symptom": str(known_symptom or {}),
                "text": text,
            }
        )

        date = result.get("date")
        if date == "今天":
            date = datetime.now().strftime("%Y-%m-%d")
        elif date == "明天":
            date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        out = {
            "intent": result.get("intent") or "register",
            "department": result.get("department"),
            "date": date,
            "period": result.get("period"),
            "confirm": bool(result.get("confirm", False)),
            "recommended_department": result.get("recommended_department"),
            "missing_fields": result.get("missing_fields") or [],
            "strategy": result.get("strategy") or "collect_missing_info",
            "triage_symptoms": result.get("triage_symptoms") or text,
            "symptom_struct": result.get("symptom_struct") or fallback["symptom_struct"],
        }

        if out["department"] and out["department"] not in settings.DEPARTMENTS:
            out["department"] = None
        if out["recommended_department"] and out["recommended_department"] not in settings.DEPARTMENTS:
            out["recommended_department"] = None
        return out
    except Exception as e:
        print(f"LLM 解析异常: {e}")
        return fallback