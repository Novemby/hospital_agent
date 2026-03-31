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
    patient_expression: str = Field(description="结合语境润色后的患者自然语言表达，供医生理解")
    objective_summary: str = Field(default="", description="客观提炼症状用于辅助病历，不包含任何风险警告或诊断建议。")
    doctor_context: str = Field(default="", description="只给医生看的历史相关辅助信息，只允许事实性回顾，不给出诊断或处置建议。")
    risk_note: str = Field(default="", description="只给医生看的历史高风险相似提醒，不包含检查、用药或处置建议。")
    patient_gentle_reminder: str = Field(default="", description="给患者看的弱提醒，只允许建议确认或补充说明，不允许给出治疗建议。")
    memory_hits: List[str] = Field(default_factory=list, description="本次命中的长期记忆关键词列表，便于调试和展示。")

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
                "2. 提取 symptom_struct。\n"
                "【重要约束】\n"
                "- 允许你使用长期记忆帮助判断科室或理解“老毛病”“又犯了”这类省略表达。\n"
                "- 但是 symptom_struct 中的 accompanying_symptoms 必须只填写本次输入里明确提到的伴随症状，绝对不能从长期记忆补写。\n"
                "- symptom_struct 中的 body_part 也优先依据本次输入里的明确部位，不要把长期记忆里的旧部位当成这次新事实。\n"
                "- 如果用户这次只说“肚子疼”，那就只能提取腹部/疼痛，不能擅自补成“反酸”“恶心”等伴随症状。\n"
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
        fallback_res = {
            "patient_expression": "，".join(words),
            "objective_summary": "",
            "doctor_context": "",
            "risk_note": "",
            "patient_gentle_reminder": "",
            "memory_hits": [],
        }
        if not self.llm or not JsonOutputParser: return fallback_res

        parser = JsonOutputParser(pydantic_object=ConsultationOutput)
        prompt = PromptTemplate(
            template=(
                "你是三甲医院的智慧医疗 Agent，正在为聋哑患者做同声传译。\n"
                "【患者长期的结构化健康画像 (JSON)】:\n{patient_profile}\n\n"
                "【当前诊间对话上下文】:\n{context}\n\n"
                "患者手语词汇：{words}\n\n"
                "【Agent 核心任务】\n"
                "1. 患者表达 (patient_expression)：结合画像和上下文翻译。若患者说“老毛病”“又犯了”，可参考 chronic_conditions 或 frequent_symptoms 补全表达，但必须保持患者原意。\n"
                "2. 病历提炼 (objective_summary)：客观提炼症状用于病历草稿。\n"
                "3. 医生辅助 (doctor_context)：如果长期记忆中存在与当前主诉明确相关的既往症状、慢病或就诊史，给出一句事实性辅助信息。只允许历史事实，不允许诊断建议、用药建议或检查建议。\n"
                "4. 风险提示 (risk_note)：只有当 risk_history 中存在与当前表达明显相似的高风险历史时，才输出一句提醒。例如“检测到与既往高风险症状相似（如尿血），建议优先关注”。不能输出检查/用药/治疗建议。\n"
                "5. 患者弱提醒 (patient_gentle_reminder)：只有在命中长期记忆且适合提醒患者补充说明时才输出。必须使用“建议确认”“建议告知医生”“建议进一步说明”这类措辞，绝对禁止出现用药、检查、治疗等建议。\n"
                "6. memory_hits：只列出本次真正命中的关键词或记忆点，最多 5 个。\n"
                "【硬约束】\n"
                "- doctor_context、risk_note、patient_gentle_reminder 在未命中相关长期记忆时必须返回空字符串。\n"
                "- doctor_context 只能写事实，如“患者历史中多次出现腹痛、恶心”。\n"
                "- risk_note 只能写“与既往高风险症状相似/相关”的提醒，不能扩展为医疗决策。\n"
                "- patient_gentle_reminder 只能写弱提醒，不能写“建议检查”“建议用药”“建议复查”。\n"
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
