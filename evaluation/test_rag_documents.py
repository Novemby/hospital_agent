import json
import tempfile
import unittest
from pathlib import Path

from backend.services.rag.documents import KnowledgeDocument, build_chunks, chunk_document, load_documents


ROOT = Path(__file__).resolve().parents[1]


class KnowledgePreparationTests(unittest.TestCase):
    def test_actual_corpus_provenance_coverage_and_stability(self):
        documents = load_documents(ROOT / "knowledge/hospital")
        self.assertEqual(len(documents), 23)
        for size in (500, 120):
            chunks = build_chunks(documents, size, min(30, size - 1))
            self.assertEqual(chunks, build_chunks(documents, size, min(30, size - 1)))
            self.assertEqual(len(chunks), len({c["chunk_id"] for c in chunks}))
            for doc in documents:
                covered = set()
                matching = [c for c in chunks if c["doc_id"] == doc.metadata["doc_id"]]
                self.assertTrue(matching)
                for chunk in matching:
                    start, end = chunk["start_char"], chunk["end_char"]
                    self.assertEqual(chunk["text"], doc.body[start:end])
                    self.assertLessEqual(len(chunk["text"]), size)
                    self.assertEqual(chunk["status"], "demo_unreviewed")
                    self.assertEqual(chunk["source_refs"], doc.metadata["source_refs"])
                    covered.update(range(start, end))
                self.assertTrue(all(i in covered for i, char in enumerate(doc.body) if not char.isspace()))

    def test_long_paragraph_and_invalid_parameters(self):
        doc = KnowledgeDocument({"doc_id": "TEST", "version": "1", "title": "标题"}, "字" * 1200, "test.md")
        chunks = chunk_document(doc, 100, 20)
        self.assertEqual(chunks[1]["start_char"], 80)
        self.assertEqual(chunks[-1]["end_char"], 1200)
        for size, overlap in ((0, 0), (100, 100), (100, -1)):
            with self.assertRaises(ValueError):
                chunk_document(doc, size, overlap)

    def test_manifest_rejects_escape_and_duplicates(self):
        manifest = json.loads((ROOT / "knowledge/hospital/manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            escaped = {**manifest, "document_count": 1,
                       "documents": [{**manifest["documents"][0], "path": "../outside.md"}]}
            (root / "manifest.json").write_text(json.dumps(escaped), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid document path"):
                load_documents(root)
        duplicate = {**manifest, "documents": manifest["documents"] + [manifest["documents"][0]],
                     "document_count": 24}
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            import shutil
            root = Path(directory) / "hospital"
            shutil.copytree(ROOT / "knowledge/hospital", root)
            (root / "manifest.json").write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate document"):
                load_documents(root)


if __name__ == "__main__":
    unittest.main()
