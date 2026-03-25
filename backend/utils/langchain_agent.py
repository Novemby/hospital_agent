import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

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
    department: Optional[str] = Field(default=None, description="科室名称，必须来自候选列表")
    date: Optional[str] = Field(default=None, description="格式 YYYY-MM-DD")
    confirm: bool = Field(default=False, description="用户是否明确确认挂号")
    triage_symptoms: Optional[str] = Field(default=None, description="症状或主诉")


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
    """LangChain 驱动的挂号语义解析，失败时自动回退规则解析。"""
    if not text:
        return _fallback_analyze(text)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not JsonOutputParser or not PromptTemplate or not ChatOpenAI:
        return _fallback_analyze(text)

    parser = JsonOutputParser(pydantic_object=RegistrationNLU)
    prompt = PromptTemplate(
        template=(
            "你是医院挂号助手的语义解析器。\n"
            "今天日期是 {today}。\n"
            "允许的科室: {departments}\n"
            "已知槽位: {known_slots}\n"
            "用户输入: {text}\n"
            "请提取意图、科室、日期、是否确认、主诉。\n"
            "日期如果是'今天'或'明天'要换成 YYYY-MM-DD。\n"
            "只能输出 JSON。\n"
            "{format_instructions}"
        ),
        input_variables=["today", "departments", "known_slots", "text"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )

    model_name = os.getenv("LC_MODEL", "gpt-4o-mini")
    llm = ChatOpenAI(model=model_name, temperature=0)

    try:
        result = (prompt | llm | parser).invoke(
            {
                "today": datetime.now().strftime("%Y-%m-%d"),
                "departments": "、".join(settings.DEPARTMENTS),
                "known_slots": str(known_slots),
                "text": text,
            }
        )
        if result.get("date") == "今天":
            result["date"] = datetime.now().strftime("%Y-%m-%d")
        elif result.get("date") == "明天":
            result["date"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        return {
            "intent": result.get("intent") or "register",
            "department": result.get("department"),
            "date": result.get("date"),
            "confirm": bool(result.get("confirm", False)),
            "triage_symptoms": result.get("triage_symptoms") or text,
        }
    except Exception:
        return _fallback_analyze(text)
