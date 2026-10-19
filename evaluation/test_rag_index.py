import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.services.rag.embeddings import APIEmbedder, EmbeddingSettings
from backend.services.rag.documents import build_chunks, load_documents
from backend.services.rag.vector_index import KnowledgeIndex, build_index, normalized_vectors, read_chunks


ROOT = Path(__file__).resolve().parents[1]


class TestEmbedder:
    """Controlled vectors verify mechanics; they are not semantic embeddings."""
    signature = {"provider": "test", "model": "fixed"}

    def embed_documents(self, texts):
        return [[3, 0], [0, 4]][:len(texts)]

    def embed_query(self, query):
        return [1, 0]


class VectorIndexTests(unittest.TestCase):
    def test_round_trip_sources_cosine_rebuild_and_stale_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            chunks = build_chunks(load_documents(ROOT / "knowledge/hospital"))[:2]
            source = root / "chunks.jsonl"
            source.write_text("\n".join(json.dumps(c) for c in chunks), encoding="utf-8")
            self.assertEqual(read_chunks(source), chunks)
            embedder = TestEmbedder()
            first = build_index(source, root / "索引", embedder)
            index = KnowledgeIndex(root / "索引", embedder, source)
            hits = index.search("test", top_k=20)
            self.assertEqual(len(hits), 2)
            self.assertEqual(hits[0]["chunk_id"], chunks[0]["chunk_id"])
            self.assertAlmostEqual(hits[0]["score"], 1)
            self.assertAlmostEqual(hits[1]["score"], 0)
            self.assertEqual(hits[0]["source_refs"], chunks[0]["source_refs"])
            second = build_index(source, root / "索引", embedder)
            self.assertNotEqual(first["generation"], second["generation"])
            self.assertEqual(KnowledgeIndex(root / "索引", embedder).index.ntotal, 2)
            other = TestEmbedder()
            other.signature = {"provider": "test", "model": "different"}
            with self.assertRaisesRegex(ValueError, "differs"):
                KnowledgeIndex(root / "索引", other)
            source.write_text(source.read_text() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Chunks changed"):
                KnowledgeIndex(root / "索引", embedder, source)
            metadata = root / "索引" / second["generation"] / "chunks.json"
            metadata.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                KnowledgeIndex(root / "索引", embedder)

    def test_invalid_vectors_and_search_arguments(self):
        for values in ([[0, 0]], [[float("nan"), 1]], [[1], [1, 2]], []):
            with self.assertRaises(ValueError):
                normalized_vectors(values, 1)
        with self.assertRaisesRegex(ValueError, "dimension"):
            normalized_vectors([[1, 2]], 1, 3)
        self.assertTrue(np.allclose(normalized_vectors([[3, 4]], 1), [[0.6, 0.8]]))


class EmbeddingAPITests(unittest.TestCase):
    def setUp(self):
        self.embedder = APIEmbedder(EmbeddingSettings("example", "https://example.com/v1", "secret", batch_size=2))

    def test_batch_order_and_request_contract(self):
        responses = [io.BytesIO(json.dumps({"data": [{"index": 1, "embedding": [0, 1]},
                                                      {"index": 0, "embedding": [1, 0]}]}).encode()),
                     io.BytesIO(json.dumps({"data": [{"index": 0, "embedding": [1, 1]}]}).encode())]
        with patch("urllib.request.urlopen", side_effect=responses) as request:
            self.assertEqual(self.embedder.embed_documents(["a", "b", "c"]), [[1, 0], [0, 1], [1, 1]])
            self.assertEqual(request.call_count, 2)
            sent = request.call_args_list[0].args[0]
            self.assertEqual(sent.full_url, "https://example.com/v1/embeddings")
            self.assertEqual(json.loads(sent.data)["input"], ["a", "b"])
            self.assertNotIn("secret", json.dumps(self.embedder.signature))

    def test_retry_and_permanent_failure(self):
        transient = urllib.error.HTTPError("url", 429, "busy", {}, None)
        response = io.BytesIO(b'{"data":[{"index":0,"embedding":[1,0]}]}')
        with patch("urllib.request.urlopen", side_effect=[transient, response]), patch("time.sleep"):
            self.assertEqual(self.embedder.embed_query("q"), [1, 0])
        unauthorized = urllib.error.HTTPError("url", 401, "secret", {}, None)
        with patch("urllib.request.urlopen", side_effect=unauthorized) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as error:
                self.embedder.embed_query("q")
            self.assertEqual(request.call_count, 1)
            self.assertNotIn("secret", str(error.exception))

    def test_duplicate_response_indices(self):
        response = io.BytesIO(b'{"data":[{"index":0,"embedding":[1]},{"index":0,"embedding":[2]}]}')
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                self.embedder.embed_documents(["a", "b"])


if __name__ == "__main__":
    unittest.main()
