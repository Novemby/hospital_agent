import os
import json
import re
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
# 🌟 长期记忆分层结构：事实层 / 模式层 / 最近就诊层 / 元信息
# ==============================================================================
class MemoryTaggedItem(BaseModel):
    value: str = ""
    source: str = Field(default="llm_inferred", description="doctor_confirmed / patient_reported / llm_inferred / system_imported / legacy_migrated")
    confirmed: bool = False
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class MemoryFacts(BaseModel):
    chronic_conditions: List[MemoryTaggedItem] = Field(default_factory=list)
    allergies: List[MemoryTaggedItem] = Field(default_factory=list)
    confirmed_diagnoses: List[MemoryTaggedItem] = Field(default_factory=list)
    confirmed_risks: List[MemoryTaggedItem] = Field(default_factory=list)


class MemoryPatterns(BaseModel):
    frequent_symptoms: List[MemoryTaggedItem] = Field(default_factory=list)
    preferred_departments: List[MemoryTaggedItem] = Field(default_factory=list)
    symptom_counts: Dict[str, int] = Field(default_factory=dict)
    risk_flags: List[MemoryTaggedItem] = Field(default_factory=list)


class RecentVisitItem(BaseModel):
    date: str = ""
    chief_complaint: str = ""
    symptoms: List[str] = Field(default_factory=list)
    department: str = ""
    risk_level: str = Field(default="NORMAL", description="枚举: NORMAL, HIGH, CRITICAL")
    source: str = Field(default="llm_inferred", description="doctor_confirmed / patient_reported / llm_inferred")
    confirmed: bool = False
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    registration_id: Optional[int] = None


class MemoryMeta(BaseModel):
    schema_version: int = 2
    updated_at: str = ""
    source: str = "llm_inferred"
    confirmed: bool = False
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class LayeredAgentMemory(BaseModel):
    facts: MemoryFacts = Field(default_factory=MemoryFacts)
    patterns: MemoryPatterns = Field(default_factory=MemoryPatterns)
    recent_visits: List[RecentVisitItem] = Field(default_factory=list)
    meta: MemoryMeta = Field(default_factory=MemoryMeta)

# ==============================================================================
# 其它基础结构体
# ==============================================================================
class SymptomStruct(BaseModel):
    primary_symptom: Optional[str] = Field(default=None)
    body_part: Optional[str] = Field(default=None)
    duration: Optional[str] = Field(default=None)
    severity: Optional[str] = Field(default=None)
    accompanying_symptoms: List[str] = Field(default_factory=list)
    negative_symptoms: List[str] = Field(default_factory=list)
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
    affirmed_symptoms: List[str] = Field(default_factory=list)
    negated_symptoms: List[str] = Field(default_factory=list)
    correction_intent: bool = False
    department_candidates: List[str] = Field(default_factory=list)

class ConsultationOutput(BaseModel):
    patient_expression: str = Field(description="结合语境润色后的患者自然语言表达，供医生理解")
    objective_summary: str = Field(default="", description="客观提炼症状用于辅助病历，不包含任何风险警告或诊断建议。")
    doctor_context: str = Field(default="", description="只给医生看的历史相关辅助信息，只允许事实性回顾，不给出诊断或处置建议。")
    risk_note: str = Field(default="", description="只给医生看的历史高风险相似提醒，不包含检查、用药或处置建议。")
    patient_gentle_reminder: str = Field(default="", description="给患者看的弱提醒，只允许建议确认或补充说明，不允许给出治疗建议。")
    memory_hits: List[str] = Field(default_factory=list, description="本次命中的长期记忆关键词列表，便于调试和展示。")

def _fallback_analyze(text: str) -> Dict[str, Any]:
    return {"intent": "register", "department": detect_department(text), "date": detect_date(text), "confirm": False, "triage_symptoms": text}


NEGATION_TOKENS = {"不", "没", "没有", "无", "并无", "不是"}


def _dedupe_keep_order(items: List[str]) -> List[str]:
    result: List[str] = []
    for item in items or []:
        value = str(item or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _normalize_confidence(value: Any, default: float = 0.7) -> float:
    try:
        conf = float(value)
    except Exception:
        conf = default
    return max(0.0, min(1.0, conf))


def _normalize_tagged_items(
    raw_items: Any,
    *,
    default_source: str,
    default_confirmed: bool,
    default_confidence: float,
) -> List[MemoryTaggedItem]:
    normalized: List[MemoryTaggedItem] = []
    seen = set()

    for raw in raw_items or []:
        if isinstance(raw, dict):
            value = str(raw.get("value") or "").strip()
            source = str(raw.get("source") or default_source)
            confirmed = bool(raw.get("confirmed", default_confirmed))
            confidence = _normalize_confidence(raw.get("confidence"), default_confidence)
        else:
            value = str(raw or "").strip()
            source = default_source
            confirmed = default_confirmed
            confidence = default_confidence

        if not value or value in seen:
            continue

        seen.add(value)
        normalized.append(
            MemoryTaggedItem(
                value=value,
                source=source,
                confirmed=confirmed,
                confidence=confidence,
            )
        )

    return normalized


def _tagged_values(items: Any) -> List[str]:
    values: List[str] = []
    for item in items or []:
        value = str(item.get("value") if isinstance(item, dict) else item or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _top_tagged_items(items: Any, limit: int) -> List[Dict[str, Any]]:
    normalized_items: List[Dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            source = str(item.get("source") or "llm_inferred")
            confirmed = bool(item.get("confirmed", False))
            confidence = _normalize_confidence(item.get("confidence"), 0.7)
        else:
            value = str(item or "").strip()
            source = "llm_inferred"
            confirmed = False
            confidence = 0.7

        if value:
            normalized_items.append({
                "value": value,
                "source": source,
                "confirmed": confirmed,
                "confidence": confidence,
            })

    normalized_items.sort(
        key=lambda item: (item["confirmed"], item["confidence"], item["value"]),
        reverse=True,
    )
    return normalized_items[:limit]


def _normalize_layered_memory(raw_memory: str = "{}") -> Dict[str, Any]:
    now_iso = datetime.now().isoformat(timespec="seconds")
    default_memory = LayeredAgentMemory(
        meta=MemoryMeta(updated_at=now_iso)
    ).model_dump()

    try:
        parsed = json.loads(raw_memory) if raw_memory else {}
    except Exception:
        parsed = {}

    if not isinstance(parsed, dict):
        return default_memory

    if {"facts", "patterns", "recent_visits", "meta"}.issubset(parsed.keys()):
        facts = parsed.get("facts", {}) if isinstance(parsed.get("facts"), dict) else {}
        patterns = parsed.get("patterns", {}) if isinstance(parsed.get("patterns"), dict) else {}
        recent_visits = parsed.get("recent_visits", []) if isinstance(parsed.get("recent_visits"), list) else []
        meta = parsed.get("meta", {}) if isinstance(parsed.get("meta"), dict) else {}
        normalized = LayeredAgentMemory(
            facts=MemoryFacts(
                chronic_conditions=_normalize_tagged_items(
                    facts.get("chronic_conditions", []),
                    default_source="legacy_migrated",
                    default_confirmed=True,
                    default_confidence=0.9,
                ),
                allergies=_normalize_tagged_items(
                    facts.get("allergies", []),
                    default_source="legacy_migrated",
                    default_confirmed=True,
                    default_confidence=0.95,
                ),
                confirmed_diagnoses=_normalize_tagged_items(
                    facts.get("confirmed_diagnoses", []),
                    default_source="legacy_migrated",
                    default_confirmed=True,
                    default_confidence=0.9,
                ),
                confirmed_risks=_normalize_tagged_items(
                    facts.get("confirmed_risks", []),
                    default_source="legacy_migrated",
                    default_confirmed=True,
                    default_confidence=0.9,
                ),
            ),
            patterns=MemoryPatterns(
                frequent_symptoms=_normalize_tagged_items(
                    patterns.get("frequent_symptoms", []),
                    default_source="legacy_migrated",
                    default_confirmed=False,
                    default_confidence=0.75,
                ),
                preferred_departments=_normalize_tagged_items(
                    patterns.get("preferred_departments", []),
                    default_source="legacy_migrated",
                    default_confirmed=False,
                    default_confidence=0.7,
                ),
                symptom_counts={
                    str(k): int(v) for k, v in (patterns.get("symptom_counts", {}) or {}).items()
                    if str(k).strip()
                },
                risk_flags=_normalize_tagged_items(
                    patterns.get("risk_flags", []),
                    default_source="legacy_migrated",
                    default_confirmed=False,
                    default_confidence=0.7,
                ),
            ),
            recent_visits=[
                RecentVisitItem(**visit) for visit in recent_visits if isinstance(visit, dict)
            ][:5],
            meta=MemoryMeta(
                schema_version=int(meta.get("schema_version", 2) or 2),
                updated_at=str(meta.get("updated_at") or now_iso),
                source=str(meta.get("source") or "llm_inferred"),
                confirmed=bool(meta.get("confirmed", False)),
                confidence=_normalize_confidence(meta.get("confidence"), 0.8),
            ),
        )
        return normalized.model_dump()

    # 兼容旧版 schema 到新版分层结构
    health_profile = parsed.get("health_profile", {}) if isinstance(parsed.get("health_profile"), dict) else {}
    risk_history = parsed.get("risk_history", []) if isinstance(parsed.get("risk_history"), list) else []
    visit_history = parsed.get("visit_history", []) if isinstance(parsed.get("visit_history"), list) else []
    preferences = parsed.get("preferences", {}) if isinstance(parsed.get("preferences"), dict) else {}

    symptom_counts: Dict[str, int] = {}
    for symptom in health_profile.get("frequent_symptoms", []) or []:
        key = str(symptom or "").strip()
        if key:
            symptom_counts[key] = symptom_counts.get(key, 0) + 1
    for visit in visit_history:
        if not isinstance(visit, dict):
            continue
        for symptom in visit.get("symptoms", []) or []:
            key = str(symptom or "").strip()
            if key:
                symptom_counts[key] = symptom_counts.get(key, 0) + 1

    normalized = LayeredAgentMemory(
        facts=MemoryFacts(
            chronic_conditions=_normalize_tagged_items(
                health_profile.get("chronic_conditions", []),
                default_source="legacy_migrated",
                default_confirmed=True,
                default_confidence=0.9,
            ),
            allergies=[],
            confirmed_diagnoses=[],
            confirmed_risks=_normalize_tagged_items(
                [str(item.get("symptom", "")).strip() for item in risk_history if isinstance(item, dict)],
                default_source="legacy_migrated",
                default_confirmed=False,
                default_confidence=0.8,
            ),
        ),
        patterns=MemoryPatterns(
            frequent_symptoms=_normalize_tagged_items(
                health_profile.get("frequent_symptoms", []),
                default_source="legacy_migrated",
                default_confirmed=False,
                default_confidence=0.75,
            ),
            preferred_departments=_normalize_tagged_items(
                [preferences.get("preferred_department", "")],
                default_source="legacy_migrated",
                default_confirmed=False,
                default_confidence=0.7,
            ),
            symptom_counts=symptom_counts,
            risk_flags=_normalize_tagged_items(
                health_profile.get("risk_flags", []),
                default_source="legacy_migrated",
                default_confirmed=False,
                default_confidence=0.7,
            ),
        ),
        recent_visits=[
            RecentVisitItem(
                date=str(visit.get("date") or ""),
                chief_complaint="、".join(_dedupe_keep_order(visit.get("symptoms", []))),
                symptoms=_dedupe_keep_order(visit.get("symptoms", [])),
                department=str(visit.get("department") or ""),
                risk_level=str(visit.get("risk_level") or "NORMAL"),
                source="legacy_migrated",
                confirmed=False,
                confidence=0.7,
            )
            for visit in visit_history if isinstance(visit, dict)
        ][:5],
        meta=MemoryMeta(updated_at=now_iso, source="legacy_migrated", confirmed=False, confidence=0.75),
    )
    return normalized.model_dump()


def _analyze_word_sequence(words: List[str]) -> Dict[str, Any]:
    normalized_words: List[str] = []
    affirmed_words: List[str] = []
    negated_words: List[str] = []
    pending_negation = False

    for raw_word in words or []:
        word = str(raw_word or "").strip()
        if not word:
            continue

        if word in NEGATION_TOKENS:
            pending_negation = True
            continue

        matched_prefix = next(
            (token for token in sorted(NEGATION_TOKENS, key=len, reverse=True) if word.startswith(token) and len(word) > len(token)),
            None,
        )
        if matched_prefix:
            symptom = word[len(matched_prefix):].strip()
            if symptom:
                negated_words.append(symptom)
                normalized_words.append(f"{matched_prefix}{symptom}")
            pending_negation = False
            continue

        if pending_negation:
            negated_words.append(word)
            normalized_words.append(f"不{word}")
            pending_negation = False
        else:
            affirmed_words.append(word)
            normalized_words.append(word)

    if pending_negation:
        normalized_words.append("不")

    return {
        "normalized_words": normalized_words,
        "affirmed_words": affirmed_words,
        "negated_words": negated_words,
        "normalized_text": "，".join(normalized_words),
    }


def _extract_last_doctor_utterance(context: str) -> str:
    lines = [line.strip() for line in str(context or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("医生：") or line.startswith("医生:"):
            return line.split("：", 1)[-1].split(":", 1)[-1].strip()
    return ""


def _normalize_patient_sentence(expr: str) -> str:
    text = str(expr or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(医生您好|医生好|您好|你好)[，,、\s]*", "", text)
    text = re.sub(r"^(我想说|我这边|我)[，,、\s]*", "", text)
    text = text.strip("，,、。；; ")
    return text


def _select_focus_answer(last_doctor_utterance: str, patient_expression: str) -> str:
    doctor = str(last_doctor_utterance or "")
    patient = str(patient_expression or "")
    if not doctor or not patient:
        return patient

    segments = [
        seg.strip("，,、。；; ")
        for seg in re.split(r"[，,。！？!?；;]", patient)
        if seg.strip("，,、。；; ")
    ]
    if not segments:
        return patient

    sleep_keywords = ["睡", "睡眠", "失眠", "入睡", "多梦", "夜间"]
    if any(keyword in doctor for keyword in ["睡得好不好", "睡得", "睡眠", "失眠", "入睡"]):
        sleep_seg = next((seg for seg in segments if any(k in seg for k in sleep_keywords)), "")
        if sleep_seg:
            if "睡不好" in sleep_seg or "睡不太好" in sleep_seg or "睡得不好" in sleep_seg:
                return "睡得不好"
            return sleep_seg

    return patient


def _postprocess_consultation_output(output: Dict[str, Any], context: str, analyzed: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(output, dict):
        return output

    expr = _normalize_patient_sentence(output.get("patient_expression", ""))
    if not expr:
        expr = str(analyzed.get("normalized_text") or "").strip()

    last_doctor = _extract_last_doctor_utterance(context)
    expr = _select_focus_answer(last_doctor, expr)
    expr = _normalize_patient_sentence(expr)

    if not expr:
        expr = str(analyzed.get("normalized_text") or "").strip()

    output["patient_expression"] = expr
    return output

class SmartHospitalAgent:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.llm = self._init_llm()

    def _init_llm(self) -> Optional["ChatOpenAI"]:
        if not self.api_key or not ChatOpenAI: return None
        return ChatOpenAI(model=os.getenv("LC_MODEL", "deepseek-chat"), temperature=0.1, api_key=self.api_key, base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com"))

    def normalize_memory_profile(self, raw_memory: str = "{}") -> Dict[str, Any]:
        return _normalize_layered_memory(raw_memory)

    def build_triage_memory_profile(self, raw_memory: str = "{}") -> str:
        memory = self.normalize_memory_profile(raw_memory)
        facts = memory.get("facts", {})
        patterns = memory.get("patterns", {})
        recent_visits = memory.get("recent_visits", [])
        triage_profile = {
            "schema_version": memory.get("meta", {}).get("schema_version", 2),
            "chronic_conditions": _top_tagged_items(facts.get("chronic_conditions", []), 5),
            "allergies": _top_tagged_items(facts.get("allergies", []), 5),
            "confirmed_risks": _top_tagged_items(facts.get("confirmed_risks", []), 5),
            "frequent_symptoms": _top_tagged_items(patterns.get("frequent_symptoms", []), 8),
            "preferred_departments": _top_tagged_items(patterns.get("preferred_departments", []), 3),
            "recent_visits": [
                {
                    "date": visit.get("date", ""),
                    "chief_complaint": visit.get("chief_complaint", ""),
                    "department": visit.get("department", ""),
                    "risk_level": visit.get("risk_level", "NORMAL"),
                    "source": visit.get("source", "llm_inferred"),
                    "confirmed": bool(visit.get("confirmed", False)),
                    "confidence": _normalize_confidence(visit.get("confidence"), 0.7),
                }
                for visit in recent_visits[:3]
            ],
        }
        return json.dumps(triage_profile, ensure_ascii=False)

    def build_consultation_memory_profile(self, raw_memory: str = "{}") -> str:
        memory = self.normalize_memory_profile(raw_memory)
        facts = memory.get("facts", {})
        patterns = memory.get("patterns", {})
        consultation_profile = {
            "schema_version": memory.get("meta", {}).get("schema_version", 2),
            "facts": facts,
            "patterns": patterns,
            "recent_visits": memory.get("recent_visits", [])[:5],
        }
        return json.dumps(consultation_profile, ensure_ascii=False)

    # --------------------------------------------------------------------------
    # 阶段一：挂号分诊 (融入结构化长期记忆)
    # --------------------------------------------------------------------------
    def triage_stage_understand(self, text: str, known_slots: Dict[str, Any], known_symptom: Optional[Dict[str, Any]] = None, patient_profile: str = "{}") -> Dict[str, Any]:
        fallback = _fallback_analyze(text)
        fallback["missing_fields"] = []
        fallback["strategy"] = "collect_missing_info"
        fallback["symptom_struct"] = {
            "primary_symptom": None,
            "body_part": None,
            "duration": None,
            "severity": None,
            "accompanying_symptoms": [],
            "negative_symptoms": [],
            "original_text": text,
        }
        fallback["affirmed_symptoms"] = []
        fallback["negated_symptoms"] = []
        fallback["correction_intent"] = False
        fallback["department_candidates"] = []
        if not text or not self.llm or not JsonOutputParser: return fallback
        known_symptom = known_symptom or {}

        parser = JsonOutputParser(pydantic_object=HybridAgentDecision)
        prompt = PromptTemplate(
            template=(
                "你是三甲医院的智慧医疗分诊 Agent。\n"
                "【患者长期的结构化健康画像 (JSON)】:\n{patient_profile}\n\n"
                "【当前会话中已收集的症状槽位(JSON)】:\n{known_symptom}\n\n"
                "用户最新输入: {text}\n"
                "【核心任务】\n"
                "1. 结合长期画像推断意图。例如用户只说了'又疼了'，你需要去画像的'frequent_symptoms'或'chronic_conditions'里寻找部位，推荐对应科室。\n"
                "2. 提取 symptom_struct。\n"
                "3. 额外输出 affirmed_symptoms、negated_symptoms、correction_intent、department_candidates，作为语义解析信号。\n"
                "【重要约束】\n"
                "- 允许你使用长期记忆帮助判断科室或理解“老毛病”“又犯了”这类省略表达。\n"
                "- 必须结合当前会话已收集的 known_symptom 理解用户是在补充、修正还是否定已有症状；不要把每句都当成全新主诉。\n"
                "- 但是 symptom_struct 中的 accompanying_symptoms 必须只填写本次输入里明确提到的伴随症状，绝对不能从长期记忆补写。\n"
                "- 如果本次输入是否定表达，例如“不头晕”“没有发烧”“无咳嗽”，这些症状绝对不能写入 primary_symptom 或 accompanying_symptoms；应写入 symptom_struct.negative_symptoms。\n"
                "- affirmed_symptoms 只放本次输入里明确存在的阳性症状；negated_symptoms 只放本次输入里明确否定的症状。\n"
                "- 如果用户在纠正前文，例如“前面说错了”“不是A，是B”，请将 correction_intent 设为 true。\n"
                "- department_candidates 按优先级输出 1 到 3 个候选科室，最可能的放前面。\n"
                "- symptom_struct 中的 body_part 也优先依据本次输入里的明确部位，不要把长期记忆里的旧部位当成这次新事实。\n"
                "- 如果用户这次只说“肚子疼”，那就只能提取腹部/疼痛，不能擅自补成“反酸”“恶心”等伴随症状。\n"
                "只输出合法的 JSON。\n{format_instructions}"
            ),
            input_variables=["patient_profile", "known_symptom", "text"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )

        try:
            result = (prompt | self.llm | parser).invoke({
                "patient_profile": patient_profile,
                "known_symptom": json.dumps(known_symptom, ensure_ascii=False),
                "text": text,
            })
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
        analyzed = _analyze_word_sequence(words)
        fallback_res = {
            "patient_expression": analyzed["normalized_text"],
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
                "患者手语词汇：{words}\n"
                "规范化后的患者词序：{normalized_words}\n"
                "明确肯定的词：{affirmed_words}\n"
                "明确否定的词：{negated_words}\n\n"
                "【Agent 核心任务】\n"
                "1. 患者表达 (patient_expression)：结合画像和上下文翻译。若患者说“老毛病”“又犯了”，可参考 chronic_conditions 或 frequent_symptoms 补全表达，但必须保持患者原意。\n"
                "2. 病历提炼 (objective_summary)：客观提炼症状用于病历草稿。\n"
                "3. 医生辅助 (doctor_context)：如果长期记忆中存在与当前主诉明确相关的既往症状、慢病或就诊史，给出一句事实性辅助信息。只允许历史事实，不允许诊断建议、用药建议或检查建议。\n"
                "4. 风险提示 (risk_note)：只有当 risk_history 中存在与当前表达明显相似的高风险历史时，才输出一句提醒。例如“检测到与既往高风险症状相似（如尿血），建议优先关注”。不能输出检查/用药/治疗建议。\n"
                "5. 患者弱提醒 (patient_gentle_reminder)：只有在命中长期记忆且适合提醒患者补充说明时才输出。必须使用“建议确认”“建议告知医生”“建议进一步说明”这类措辞，绝对禁止出现用药、检查、治疗等建议。\n"
                "6. memory_hits：只列出本次真正命中的关键词或记忆点，最多 5 个。\n"
                "【硬约束】\n"
                "- patient_expression 必须优先回答“医生上一句问题”的核心意图，使用最小必要信息，避免重复医生已知或刚刚说过的旧症状。\n"
                "- 当医生在追问单一维度（如“睡得好不好”“持续多久”）时，patient_expression 只保留该维度答案。示例：医生问“睡得好不好”，患者词含“头晕、痛、睡不好”，输出应为“睡得不好”。\n"
                "- patient_expression 需简洁自然，建议 6~18 个汉字，禁止礼貌寒暄开头（如“医生您好”）和“另外、还有”等冗余连接。\n"
                "- doctor_context、risk_note、patient_gentle_reminder 在未命中相关长期记忆时必须返回空字符串。\n"
                "- doctor_context 只能写事实，如“患者历史中多次出现腹痛、恶心”。\n"
                "- risk_note 只能写“与既往高风险症状相似/相关”的提醒，不能扩展为医疗决策。\n"
                "- patient_gentle_reminder 只能写弱提醒，不能写“建议检查”“建议用药”“建议复查”。\n"
                "- negated_words 中的项目必须按否定理解，绝对不能在 patient_expression 或 objective_summary 中改写成患者“有”这个症状。\n"
                "- 例如 words=不、头晕、咳嗽 时，正确语义是“患者表示不头晕，但有咳嗽”，绝不能写成“头晕且咳嗽”。\n"
                "\n必须严格按照 JSON 格式输出：\n{format_instructions}"
            ),
            input_variables=["context", "words", "normalized_words", "affirmed_words", "negated_words", "patient_profile"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        try:
            raw_output = (prompt | self.llm | parser).invoke({
                "context": context or "无上下文",
                "words": "、".join(words),
                "normalized_words": "、".join(analyzed["normalized_words"]),
                "affirmed_words": "、".join(analyzed["affirmed_words"]) or "无",
                "negated_words": "、".join(analyzed["negated_words"]) or "无",
                "patient_profile": patient_profile,
            })
            return _postprocess_consultation_output(raw_output, context, analyzed)
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

        normalized_old_memory = self.normalize_memory_profile(old_memory)
        parser = JsonOutputParser(pydantic_object=LayeredAgentMemory)
        prompt = PromptTemplate(
            template=(
                "你是医疗Agent的记忆管理中枢。患者本次就诊已结束。\n"
                "【该患者以前的分层记忆画像 (JSON)】:\n{old_memory}\n\n"
                "【本次就诊完整对话】:\n{chat_history}\n\n"
                "【核心任务】\n"
                "请将本次对话中暴露的病情、医生诊断、新症状等，**融入并更新**到患者的分层记忆画像中。\n"
                "要求：\n"
                "1. facts / patterns 中的每个项目都必须写成对象，并包含 value、source、confirmed、confidence 四个字段。\n"
                "2. facts 只保存高可信、相对稳定的事实，例如慢病、过敏、医生明确确认的重要风险；不要把本次未确认推断写入 facts。\n"
                "3. patterns 保存高频症状、常见科室偏好和 symptom_counts；它们只能作为模式，不代表本次事实。\n"
                "4. recent_visits 在头部追加本次就诊摘要（最多保留近5次），并填写 source、confirmed、confidence。\n"
                "5. source 只能使用 doctor_confirmed、patient_reported、llm_inferred 之一；如果无法确定，优先 patient_reported 或 llm_inferred。\n"
                "6. confirmed 只有在医生明确确认或稳定硬事实时才可为 true。\n"
                "7. confidence 使用 0 到 1 之间的小数：医生确认通常 >=0.9，患者明确陈述通常 0.7-0.85，模型推断通常 <=0.7。\n"
                "8. meta.schema_version 固定为 2，meta.updated_at 使用当前时间，meta.source 写 llm_inferred，meta.confirmed=false。\n"
                "9. 将所有总结和提取提炼成简短结构化短语，绝对不存长段落。\n"
                "10. 必须输出合法的 JSON。\n{format_instructions}"
            ),
            input_variables=["chat_history", "old_memory"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        try:
            result = (prompt | self.llm | parser).invoke({
                "chat_history": chat_history,
                "old_memory": json.dumps(normalized_old_memory, ensure_ascii=False),
            })
            normalized_result = self.normalize_memory_profile(json.dumps(result, ensure_ascii=False))
            return json.dumps(normalized_result, ensure_ascii=False)
        except Exception as e:
            print(f"❌ 记忆固化异常: {e}")
            return json.dumps(normalized_old_memory, ensure_ascii=False)

# 全局单例
unified_medical_agent = SmartHospitalAgent()
