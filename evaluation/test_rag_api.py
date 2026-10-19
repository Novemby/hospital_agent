import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.rag_app import app


class KnowledgeAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_valid_request_uses_service(self):
        with patch("backend.services.rag.answers.RAGAnswerService.from_env") as factory:
            factory.return_value.answer.return_value = {"status": "answered", "answer": "说明 [S1]", "sources": []}
            response = self.client.post("/api/knowledge/chat", json={"question": "如何挂号", "top_k": 3})
            self.assertEqual(response.status_code, 200)
            factory.return_value.answer.assert_called_once_with("如何挂号", 3)

    def test_invalid_input_and_missing_index(self):
        for payload in ({"question": ""}, {"question": "问", "top_k": 9}, {"question": "问" * 2001}):
            self.assertEqual(self.client.post("/api/knowledge/chat", json=payload).status_code, 422)
        with patch("backend.services.rag.answers.RAGAnswerService.from_env", side_effect=FileNotFoundError("private path")):
            response = self.client.post("/api/knowledge/chat", json={"question": "问"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private path", response.text)


if __name__ == "__main__":
    unittest.main()
