import unittest
from backend.services.rag.answers import RAGAnswerService, validate_answer


HIT = {"doc_id": "ACCESS-002", "chunk_id": "chunk1", "title": "手语识别", "section": "错误处理",
       "status": "demo_unreviewed", "text": "识别错误时请核对结果，重试或改用文字输入。", "source_path": "accessibility/sign_input.md", "score": 0.7}
PAYLOAD = {"status": "answered", "claims": [{"text": "识别错误时可以改用文字输入。", "evidence": [
    {"source_id": "S1", "quote": "重试或改用文字输入。"}]}], "follow_up": ""}


class Index:
    def search(self, question, top_k):
        return [HIT]


class Chat:
    def generate(self, question, hits):
        return PAYLOAD


class AnswerTests(unittest.TestCase):
    def test_server_citations_and_source_metadata(self):
        response = RAGAnswerService(Index(), Chat).answer("识别错了怎么办")
        self.assertEqual(response["status"], "answered")
        self.assertIn("[S1]", response["answer"])
        self.assertEqual(response["sources"][0]["chunk_id"], "chunk1")
        self.assertIn("未审核", response["scope_notice"])
        self.assertIn("total", response["timings_ms"])

    def test_unknown_source_fabricated_quote_and_uncited_answer_rejected(self):
        for evidence in ([{"source_id": "S99", "quote": "重试或改用文字输入。"}],
                         [{"source_id": "S1", "quote": "前往一楼免费领取设备。"}], []):
            with self.assertRaises(ValueError):
                validate_answer({**PAYLOAD, "claims": [{"text": "说明", "evidence": evidence}]}, [HIT])
        with self.assertRaises(ValueError):
            validate_answer({**PAYLOAD, "follow_up": "本院费用是100元。"}, [HIT])

    def test_clarification_unknown_and_business_statuses(self):
        for status in ("clarify", "insufficient", "business_query"):
            response = validate_answer({"status": status, "claims": [], "follow_up": "请向工作人员核实。"}, [HIT])
            self.assertEqual(response["status"], status)
            self.assertEqual(response["sources"], [])

    def test_generation_and_retrieval_fallbacks(self):
        class FailedChat:
            def generate(self, question, hits):
                raise RuntimeError("timeout")
        result = RAGAnswerService(Index(), FailedChat).answer("问题")
        self.assertEqual(result["status"], "generation_unavailable")
        self.assertEqual(result["sources"], [])
        self.assertEqual(len(result["retrieved_sources"]), 1)
        class FailedIndex:
            def search(self, question, top_k):
                raise RuntimeError("timeout")
        result = RAGAnswerService(FailedIndex(), Chat).answer("问题")
        self.assertEqual(result["status"], "retrieval_unavailable")

    def test_blank_and_excessive_input(self):
        service = RAGAnswerService(Index(), Chat)
        for question, k in ((" ", 3), ("问" * 2001, 3), ("问", 0), ("问", 9)):
            with self.assertRaises(ValueError):
                service.answer(question, k)


if __name__ == "__main__":
    unittest.main()
