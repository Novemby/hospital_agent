"""LangGraph orchestration over existing registration and RAG services.

The caller owns session storage/locking. No database handles enter checkpoints.
"""
import copy
import logging
import re
import time
from typing import Any, Callable, Dict, TypedDict

from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)
CONFIRM_WORDS = {"确认", "确认挂号", "确定挂号", "ok", "yes", "y"}
RESUME_WORDS = {"继续挂号", "返回挂号", "恢复挂号"}
CANCEL_WORDS = {"取消", "取消挂号", "停止挂号"}


class WorkflowState(TypedDict, total=False):
    message: str
    route: str
    response: Dict[str, Any]
    trace: list


def select_route(message, session):
    text = message.strip().rstrip("。！!？?")
    meta = session.get("workflow", {})
    if text in CANCEL_WORDS:
        return "cancel"
    if text in RESUME_WORDS:
        return "resume"
    if text.lower() in CONFIRM_WORDS:
        return "direct" if meta.get("consultation_active") else "registration"
    if re.search(r"(剩.*号|还有.*号|号源.*(多少|几个|剩)|排队.*(进度|几个|多少))", text):
        return "business_query"
    # Explicit edits/new registration take precedence over pending RAG clarification.
    if re.search(r"(我要挂|我想挂|帮我挂|挂.*科|换科室|改.*(科|上午|下午|今天|明天)|重新描述)", text):
        if not re.search(r"(怎么|如何|流程|须知|准备)", text):
            return "registration"
    if text in {"你好", "您好", "谢谢", "谢谢你", "帮助"}:
        return "direct"
    if meta.get("pending_question"):
        return "knowledge"
    if re.search(r"(怎么办|怎么|如何|流程|须知|准备|叫号|轮椅|翻译|无障碍|检查|抽血|B超|超声|CT|MRI|报告|服务|费用|几点|在哪)", text, re.I):
        return "knowledge"
    return "registration"


def run_workflow(message, session, registration: Callable, knowledge: Callable, business_query: Callable):
    """Callbacks are bound per request; synchronous graph executes one turn."""
    meta = session.setdefault("workflow", {})

    def response(reply, options=None, action=None, **extra):
        return {"reply": reply, "state": session["state"], "slots": copy.deepcopy(session["slots"]),
                "required_fields": [], "options": options or [], "next_action": action, **extra}

    def timed(name, function):
        def node(state):
            started = time.perf_counter()
            update = function(state)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            update["trace"] = state.get("trace", []) + [{"node": name, "elapsed_ms": elapsed}]
            logger.info("hospital_graph node=%s elapsed_ms=%s", name, elapsed)
            return update
        return node

    def route_node(state):
        return {"route": select_route(state["message"], session)}

    def registration_node(state):
        meta.pop("pending_question", None)
        meta["consultation_active"] = False
        text = state["message"]
        if session["state"] == "SUCCESS":
            return {"response": response("此会话已挂号成功，请勿重复提交。新业务请开启新会话。", action="done")}
        if session["state"] == "SUBMIT":
            return {"response": response("上次提交结果尚未确认，请核实挂号记录后再操作。", action="verify_registration")}
        # LLM inference is never authorization to create a registration.
        if session["state"] == "CONFIRM" and text.strip().rstrip("。！!").lower() not in CONFIRM_WORDS:
            session["state"] = "COLLECT_INFO"
            session["slots"]["schedule_id"] = None
        result = registration(text)
        meta["last_registration_response"] = copy.deepcopy(result)
        return {"response": result}

    def knowledge_node(state):
        pending = meta.get("pending_question")
        question = (f"原问题：{pending}\n用户补充：{state['message']}" if pending else state["message"])
        result = knowledge(question)
        meta["consultation_active"] = True
        if result["status"] == "clarify":
            # Bound context; never put consultation into patient symptom slots.
            meta["pending_question"] = question[-1500:]
        elif result["status"] not in ("retrieval_unavailable", "generation_unavailable"):
            meta.pop("pending_question", None)
        return {"response": response(result["answer"], ["继续挂号"], "knowledge_answer",
                                     rag_status=result["status"], sources=result.get("sources", []),
                                     retrieved_sources=result.get("retrieved_sources", []),
                                     scope_notice=result.get("scope_notice", ""))}

    def resume_node(state):
        meta.pop("pending_question", None)
        meta["consultation_active"] = False
        previous = meta.get("last_registration_response")
        if previous and session["state"] not in ("SUCCESS", "SUBMIT"):
            result = copy.deepcopy(previous)
            result["reply"] = "已返回挂号流程。" + result["reply"]
            result["state"] = session["state"]
            result["slots"] = copy.deepcopy(session["slots"])
            return {"response": result}
        return {"response": response("请描述挂号需求，例如科室、日期和时段。" if session["state"] == "INIT" else "此会话已提交，请先核实挂号结果。", action="resume_registration")}

    def direct_node(state):
        reply = "您好，可以描述挂号需求，或询问医院服务知识。"
        if state["message"].strip().lower() in CONFIRM_WORDS and meta.get("consultation_active"):
            reply = "当前是知识咨询。若要确认挂号，请先选择“继续挂号”，核对信息后再确认。"
        elif "谢谢" in state["message"]:
            reply = "不客气。需要时可以继续咨询或返回挂号。"
        return {"response": response(reply, ["继续挂号"] if meta.get("consultation_active") else [])}

    def cancel_node(state):
        if session["state"] in ("SUCCESS", "SUBMIT"):
            return {"response": response("此会话已经提交，取消对话不能撤销挂号，请通过医院业务渠道核实。", action="verify_registration")}
        session.update({"state": "INIT", "slots": {"department": None, "date": None, "triage_symptoms": None}, "last_options": []})
        session.pop("symptom_struct", None)
        session.pop("policy", None)
        meta.clear()
        return {"response": response("已取消本次尚未提交的挂号流程。您可以重新描述需求。", action="cancelled")}

    def business_node(state):
        # Reads may use temporary parameters, but never replace pending registration slots.
        meta["consultation_active"] = True
        meta.pop("pending_question", None)
        return {"response": response(business_query(state["message"], copy.deepcopy(session["slots"])),
                                     ["继续挂号"], "business_query")}

    graph = StateGraph(WorkflowState)
    graph.add_node("classify", timed("route", route_node))
    nodes = {"registration": registration_node, "knowledge": knowledge_node, "resume": resume_node,
             "direct": direct_node, "cancel": cancel_node, "business_query": business_node}
    for name, function in nodes.items():
        graph.add_node(name, timed(name, function))
        graph.add_edge(name, END)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", lambda state: state["route"], {name: name for name in nodes})
    result = graph.compile().invoke({"message": message, "trace": []})
    return {**result["response"], "workflow_route": result["route"], "workflow_trace": result["trace"]}
