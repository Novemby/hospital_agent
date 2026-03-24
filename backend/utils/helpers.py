from datetime import datetime, timedelta
from typing import Optional, Dict
from ..config import settings

def detect_department(text: str) -> Optional[str]:
    for dept in settings.DEPARTMENTS:
        if dept in text:
            return dept
    return None

def detect_date(text: str) -> Optional[str]:
    if "今天" in text:
        return datetime.now().strftime("%Y-%m-%d")
    if "明天" in text:
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return None

# Agent 会话存储（简化版，后续可替换为 Redis）
agent_sessions: Dict[str, dict] = {}

def get_or_create_agent_session(session_id: str, patient_id: int) -> dict:
    if session_id not in agent_sessions:
        agent_sessions[session_id] = {
            "patient_id": patient_id,
            "state": "INIT",
            "slots": {
                "department": None,
                "date": None,
                "triage_symptoms": None,
            },
            "last_options": [],
        }
    return agent_sessions[session_id]

def make_agent_response(reply: str, session: dict, required_fields=None, options=None, next_action=None):
    return {
        "reply": reply,
        "state": session["state"],
        "required_fields": required_fields or [],
        "options": options or [],
        "next_action": next_action,
        "slots": session["slots"],
    }