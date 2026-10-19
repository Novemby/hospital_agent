import copy
import unittest

from backend.services.hospital_workflow import run_workflow, select_route


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.session = {"state": "INIT", "patient_id": 1,
                        "slots": {"department": None, "date": None, "triage_symptoms": None}}
        self.reg_calls, self.knowledge_calls, self.business_calls = [], [], []
        self.submissions = 0

    def registration(self, text):
        self.reg_calls.append(text)
        if text == "确认" and self.session["state"] == "CONFIRM":
            self.submissions += 1
            self.session["state"] = "SUCCESS"
            return {"reply": "成功", "state": "SUCCESS", "slots": copy.deepcopy(self.session["slots"]),
                    "options": [], "next_action": "go_waiting"}
        self.session["state"] = "CONFIRM"
        self.session["slots"].update({"department": "耳鼻喉科", "date": "2026-10-11", "period": "上午", "schedule_id": 1})
        return {"reply": "核对信息后确认", "state": "CONFIRM", "slots": copy.deepcopy(self.session["slots"]),
                "options": ["确认"], "next_action": "confirm_registration"}

    def knowledge(self, question):
        self.knowledge_calls.append(question)
        return {"status": "answered", "answer": "知识说明 [S1]", "sources": [{"source_id": "S1"}]}

    def business(self, message, slots):
        self.business_calls.append(message)
        slots["department"] = "其他科室"
        return "上午剩余5个号"

    def turn(self, message, knowledge=None):
        return run_workflow(message, self.session, self.registration, knowledge or self.knowledge, self.business)

    def test_registration_consultation_resume_confirm_and_repeat(self):
        self.turn("我想挂明天上午耳鼻喉科")
        saved = copy.deepcopy(self.session["slots"])
        result = self.turn("听不清叫号怎么办？")
        self.assertEqual(result["workflow_route"], "knowledge")
        self.assertEqual(result["sources"][0]["source_id"], "S1")
        self.assertEqual(self.session["slots"], saved)
        self.turn("确认")
        self.assertEqual(self.submissions, 0)
        resume = self.turn("继续挂号")
        self.assertEqual(resume["options"], ["确认"])
        self.assertEqual(len(self.reg_calls), 1)
        self.turn("确认")
        self.turn("确认")
        self.assertEqual(self.submissions, 1)

    def test_clarification_context_does_not_pollute_registration(self):
        def clarify(question):
            self.knowledge_calls.append(question)
            return {"status": "clarify", "answer": "检查哪个部位？", "sources": []}
        self.turn("我想挂耳鼻喉科")
        saved = copy.deepcopy(self.session["slots"])
        self.turn("做B超需要憋尿吗？", clarify)
        self.turn("腹部超声")
        self.assertIn("原问题：做B超需要憋尿吗？", self.knowledge_calls[-1])
        self.assertEqual(self.session["slots"], saved)
        self.assertNotIn("pending_question", self.session["workflow"])

    def test_cancel_edit_and_business_query(self):
        self.turn("我想挂耳鼻喉科")
        saved = copy.deepcopy(self.session["slots"])
        self.turn("明天耳鼻喉科还剩几个号？")
        self.assertEqual(len(self.business_calls), 1)
        self.assertEqual(self.session["slots"], saved)
        self.turn("确认")
        self.assertEqual(self.submissions, 0)
        self.turn("改成明天下午")
        self.assertEqual(len(self.reg_calls), 2)
        self.turn("取消挂号")
        self.assertEqual(self.session["state"], "INIT")
        self.assertIsNone(self.session["slots"]["department"])
        self.assertFalse(self.session["workflow"])

    def test_completed_registration_cannot_cancel_or_resubmit(self):
        self.turn("我想挂耳鼻喉科")
        self.turn("确认")
        self.turn("取消挂号")
        self.assertEqual(self.session["state"], "SUCCESS")
        self.turn("确认")
        self.assertEqual(self.submissions, 1)

    def test_failed_rag_and_trace(self):
        self.turn("我想挂耳鼻喉科")
        def failed(question):
            return {"status": "retrieval_unavailable", "answer": "暂不可用", "sources": []}
        result = self.turn("手语识别错了怎么办？", failed)
        self.assertEqual([item["node"] for item in result["workflow_trace"]], ["route", "knowledge"])
        self.assertEqual(self.turn("继续挂号")["state"], "CONFIRM")

    def test_rules_and_confirmation_questions(self):
        for message, route in (("你好", "direct"), ("挂号怎么确认？", "knowledge"),
                               ("我头疼三天了", "registration"), ("取消挂号", "cancel")):
            self.assertEqual(select_route(message, self.session), route)


if __name__ == "__main__":
    unittest.main()
