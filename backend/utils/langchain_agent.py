import os
import json
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

# ==============================================================================
# 🌟 新增：Agent 长期记忆流 (Long-Term Memory Stream) 结构化定义
# 完全按照你的设计，分为：就诊历史、健康特征、风险记录、面诊摘要、行为偏好
# ==============================================================================
class VisitHistoryItem(BaseModel):
    date: str
    symptoms: List[str]
    department: str
    risk_level: str = Field(description="枚举: NORMAL, HIGH, CRITICAL")

class HealthProfile(BaseModel):
    chronic_conditions: List[str]
    frequent_symptoms: List[str]
    risk_flags: List[str]

class RiskHistoryItem(BaseModel):
    symptom: str
    date: str
    level: str = Field(description="枚举: HIGH, CRITICAL")

class ConsultationSummaryItem(BaseModel):
    chief_complaint: str
    assessment: str
    recommendations: List[str]

class Preferences(BaseModel):
    preferred_department: str
    communication_style: str

class AgentStructuredMemory(BaseModel):
    visit_history: List[VisitHistoryItem]
    health_profile: HealthProfile
    risk_history: List[RiskHistoryItem]
    consultation_summary: List[ConsultationSummaryItem]
    preferences: Preferences

# ==============================================================================
# 其它基础结构体
# ==============================================================================
class SymptomStruct(BaseModel):
    primary_symptom: Optional[str] = Field(default=None)
    body_part: Optional[str] = Field(default=None)
    duration: Optional[str] = Field(default=None)
    severity: Optional[str] = Field(default=None)
    accompanying_symptoms: List[str] = Field(default_factory=list)
    original_text: Optional[str] = Field(default=None)

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

class ConsultationOutput(BaseModel):
    polished_text: str = Field(description="结合语境润色后的通顺患者自然语言")
    objective_summary: str = Field(default="", description="客观提炼症状用于辅助病历，不包含任何风险警告或诊断建议。")

def _fallback_analyze(text: str) -> Dict[str, Any]:
    return {"intent": "register", "department": detect_department(text), "date": detect_date(text), "confirm": False, "triage_symptoms": text}

class SmartHospitalAgent:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.llm = self._init_llm()

    def _init_llm(self) -> Optional["ChatOpenAI"]:
        if not self.api_key or not ChatOpenAI: return None
        return ChatOpenAI(model=os.getenv("LC_MODEL", "deepseek-chat"), temperature=0.1, api_key=self.api_key, base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"))

    # --------------------------------------------------------------------------
    # 阶段一：挂号分诊 (融入结构化长期记忆)
    # --------------------------------------------------------------------------
    def triage_stage_understand(self, text: str, known_slots: Dict[str, Any], known_symptom: Optional[Dict[str, Any]] = None, patient_profile: str = "{}") -> Dict[str, Any]:
        fallback = _fallback_analyze(text)
        fallback["missing_fields"] = []
        fallback["strategy"] = "collect_missing_info"
        fallback["symptom_struct"] = {"primary_symptom": None, "body_part": None, "duration": None, "severity": None, "accompanying_symptoms": [], "original_text": text}
        if not text or not self.llm or not JsonOutputParser: return fallback

        parser = JsonOutputParser(pydantic_object=HybridAgentDecision)
        prompt = PromptTemplate(
            template=(
                "你是三甲医院的智慧医疗分诊 Agent。\n"
                "【患者长期的结构化健康画像 (JSON)】:\n{patient_profile}\n\n"
                "用户最新输入: {text}\n"
                "【核心任务】\n"
                "1. 结合长期画像推断意图。例如用户只说了'又疼了'，你需要去画像的'frequent_symptoms'或'chronic_conditions'里寻找部位，推荐对应科室。\n"
                "2. 提取 symptom_struct。如果语意模糊，参考长期画像补全。\n"
                "只输出合法的 JSON。\n{format_instructions}"
            ),
            input_variables=["patient_profile", "text"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )

        try:
            result = (prompt | self.llm | parser).invoke({"patient_profile": patient_profile, "text": text})
            # 处理日期等省略
            date = result.get("date")
            if date == "今天": date = datetime.now().strftime("%Y-%m-%d")
            elif date == "明天": date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            out = { **fallback, **result, "date": date }
            return out
        except Exception as e:
            print(f"❌ 分诊 Agent 异常: {e}")
            return fallback

    # --------------------------------------------------------------------------
    # 阶段二：面诊同传 (融入结构化长期记忆，且静默不报警)
    # --------------------------------------------------------------------------
    def consultation_stage_polish(self, words: List[str], context: str, patient_profile: str = "{}") -> Dict[str, str]:
        fallback_res = {"polished_text": "，".join(words), "objective_summary": ""}
        if not self.llm or not JsonOutputParser: return fallback_res

        parser = JsonOutputParser(pydantic_object=ConsultationOutput)
        prompt = PromptTemplate(
            template=(
                "你是三甲医院的智慧医疗 Agent，正在为聋哑患者做同声传译。\n"
                "【患者长期的结构化健康画像 (JSON)】:\n{patient_profile}\n\n"
                "【当前诊间对话上下文】:\n{context}\n\n"
                "患者手语词汇：{words}\n\n"
                "【Agent 核心任务】\n"
                "1. 翻译润色 (polished_text)：结合画像和上下文翻译。若患者说'老毛病'，请根据画像中的 chronic_conditions 翻译出具体病名。\n"
                "2. 病历提炼 (objective_summary)：作为静默辅助，客观提炼症状用于病历草稿。\n"
                "⚠️ 绝对禁止在输出中包含针对医生的风险警告或教导！你只是个底层的理解引擎。\n"
                "\n必须严格按照 JSON 格式输出：\n{format_instructions}"
            ),
            input_variables=["context", "words", "patient_profile"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        try:
            return (prompt | self.llm | parser).invoke({
                "context": context or "无上下文", "words": "、".join(words), "patient_profile": patient_profile
            })
        except Exception as e:
            print(f"❌ 面诊 Agent 异常: {e}")
            return fallback_res

    # --------------------------------------------------------------------------
    # 阶段三：划词解释 (保持不变)
    # --------------------------------------------------------------------------
    def explain_medical_term(self, term: str, context: str) -> str:
        if not self.llm: return "无法解释"
        prompt = PromptTemplate(
            template="请用大白话解释术语【{term}】，结合上下文：\n{context}\n字数100字左右，不加客套话。",
            input_variables=["term", "context"]
        )
        try:
            return (prompt | self.llm).invoke({"term": term, "context": context}).content.strip()
        except: return "无法解释"

    # --------------------------------------------------------------------------
    # 阶段四：记忆固化 (Memory Consolidation) - 🌟 进化为 JSON 更新引擎
    # --------------------------------------------------------------------------
    def consolidate_memory(self, chat_history: str, old_memory: str = "{}") -> str:
        if not self.llm or not JsonOutputParser: return "{}"

        parser = JsonOutputParser(pydantic_object=AgentStructuredMemory)
        prompt = PromptTemplate(
            template=(
                "你是医疗Agent的记忆管理中枢。患者本次就诊已结束。\n"
                "【该患者以前的记忆画像 (JSON)】:\n{old_memory}\n\n"
                "【本次就诊完整对话】:\n{chat_history}\n\n"
                "【核心任务】\n"
                "请将本次对话中暴露的病情、医生诊断、新症状等，**融入并更新**到患者的记忆画像中。\n"
                "要求：\n"
                "1. 保留原有不可变的特征（如慢性病、历史风险）。\n"
                "2. 在 visit_history 头部追加本次的记录（最多保留近5次）。\n"
                "3. 将所有的总结和提取提炼成简短的结构化短语，绝对不存长文本！\n"
                "4. 必须输出合法的 JSON。\n{format_instructions}"
            ),
            input_variables=["chat_history", "old_memory"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        try:
            result = (prompt | self.llm | parser).invoke({"chat_history": chat_history, "old_memory": old_memory})
            # 将字典转换回 JSON 字符串存入数据库
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            print(f"❌ 记忆固化异常: {e}")
            return old_memory

# 全局单例
unified_medical_agent = SmartHospitalAgent()