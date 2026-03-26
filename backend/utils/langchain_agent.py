import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from pydantic import BaseModel, Field
from ..config import settings
from .helpers import detect_date, detect_department

try:
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import PromptTemplate
    from langchain_openai import ChatOpenAI
except Exception as e:
    print(f"⚠️ 大模型核心依赖包缺失: {e}")
    JsonOutputParser = None
    PromptTemplate = None
    ChatOpenAI = None


class SymptomStruct(BaseModel):
    primary_symptom: Optional[str] = Field(default=None,
                                           description="最主要的不适感受（如'头晕','头痛','发热','胸闷'等）。只要患者表达了不适，此项绝不能为null！")
    body_part: Optional[str] = Field(default=None, description="具体身体部位及方位，如'左肩', '右上腹'")
    duration: Optional[str] = Field(default=None, description="持续时间，如'1天内', '1周', '几个月'")
    severity: Optional[str] = Field(default=None, description="严重程度描述，如'轻微', '剧烈'")
    accompanying_symptoms: List[str] = Field(default_factory=list, description="伴随的其他症状列表")
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


def _fallback_analyze(text: str) -> Dict[str, Any]:
    lower_text = text.lower()
    return {
        "intent": "confirm" if ("确认" in text or lower_text in ["ok", "yes", "y"]) else "register",
        "department": detect_department(text),
        "date": detect_date(text),
        "confirm": ("确认" in text or lower_text in ["ok", "yes", "y"]),
        "triage_symptoms": text if text else None,
    }


# ==============================================================================
# 🌟 核心重构：统一智慧医疗智能体 (Unified Smart Hospital Agent)
# 完美回答老师的问题：这是一个拥有生命周期管理的统一 Agent！
# ==============================================================================
class SmartHospitalAgent:
    def __init__(self):
        """初始化同一个智能体大脑"""
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.llm = self._init_llm()

    def _init_llm(self) -> Optional["ChatOpenAI"]:
        if not self.api_key or not ChatOpenAI:
            return None
        return ChatOpenAI(
            model=os.getenv("LC_MODEL", "deepseek-chat"),
            temperature=0.1,  # 降低温度，保证各阶段输出稳定性
            api_key=self.api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"),
        )

    # --------------------------------------------------------------------------
    # 阶段一：挂号分诊阶段 (Triage Stage)
    # 任务：主动追问、结构化信息提取
    # --------------------------------------------------------------------------
    def triage_stage_understand(self, text: str, known_slots: Dict[str, Any],
                                known_symptom: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                "primary_symptom": None, "body_part": None, "duration": None,
                "severity": None, "accompanying_symptoms": [], "original_text": text,
            },
        }

        if not text or not self.llm or not JsonOutputParser:
            return fallback

        parser = JsonOutputParser(pydantic_object=HybridAgentDecision)
        prompt = PromptTemplate(
            template=(
                "你是三甲医院的统一智慧医疗 Agent，当前处于【大厅挂号分诊阶段】。\n"
                "今天日期: {today}\n"
                "可用科室白名单: {departments}\n"
                "当前已知槽位: {known_slots}\n"
                "当前已知症状结构: {known_symptom}\n"
                "用户最新输入: {text}\n"
                "【核心任务】\n"
                "1. 推断意图与科室。如果只有症状，推荐最合适白名单科室。\n"
                "2. 精准提取 symptom_struct 字段。只要表达了身体不适，primary_symptom 绝不能为null！保留原话。\n"
                "3. 绝对不允许捏造症状。\n"
                "只输出合法的 JSON。\n{format_instructions}"
            ),
            input_variables=["today", "departments", "known_slots", "known_symptom", "text"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )

        try:
            result = (prompt | self.llm | parser).invoke({
                "today": datetime.now().strftime("%Y-%m-%d"),
                "departments": "、".join(settings.DEPARTMENTS),
                "known_slots": str(known_slots or {}),
                "known_symptom": str(known_symptom or {}),
                "text": text,
            })

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
            if out["department"] not in settings.DEPARTMENTS: out["department"] = None
            if out["recommended_department"] not in settings.DEPARTMENTS: out["recommended_department"] = None
            return out
        except Exception as e:
            print(f"❌ 分诊阶段 Agent 解析异常: {e}")
            return fallback

    # --------------------------------------------------------------------------
    # 阶段二：诊间面诊阶段 (Consultation Stage)
    # 任务：放弃追问，转为结合上下文的同声传译与润色
    # --------------------------------------------------------------------------
    def consultation_stage_polish(self, words: List[str], context: str) -> str:
        fallback_text = "，".join(words)
        if not self.llm or not PromptTemplate:
            return fallback_text

        prompt = PromptTemplate(
            template=(
                "你是三甲医院的统一智慧医疗 Agent，当前处于【诊间面诊同传阶段】。\n"
                "患者是聋哑人，现在正坐在医生面前面诊。\n"
                "这是最近的医患聊天记录上下文：\n"
                "=====\n{context}\n=====\n\n"
                "患者刚才比划了以下几个零碎的手语词汇：{words}\n\n"
                "【任务】\n"
                "请结合医生的上下文，将患者的手语词汇翻译成一句自然、通顺、符合语境的话。\n"
                "要求：\n"
                "1. 语气必须是患者对医生说话的礼貌口吻（如“医生您好”，“我感觉”）。\n"
                "2. 绝对不能超出患者提供的词汇范畴去捏造病情！\n"
                "3. 只输出润色后的一句话，不要输出任何多余的解释。"
            ),
            input_variables=["context", "words"]
        )

        try:
            result = (prompt | self.llm).invoke({
                "context": context or "无上下文",
                "words": "、".join(words)
            })
            return result.content.strip()
        except Exception as e:
            print(f"❌ 面诊阶段 Agent 润色异常: {e}")
            return fallback_text


# 全局单例实例化，确保整个系统运行期间是“同一个 Agent”
unified_medical_agent = SmartHospitalAgent()