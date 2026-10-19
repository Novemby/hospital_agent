"""Development smoke check: document-level retrieval, not answer evaluation."""

import argparse
import json
import time
from pathlib import Path

from backend.services.rag.embeddings import APIEmbedder, EmbeddingSettings
from backend.services.rag.vector_index import KnowledgeIndex


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run the existing retrieval smoke cases")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    index = KnowledgeIndex(root / "data/rag/index", APIEmbedder(EmbeddingSettings.from_env()),
                           root / "data/rag/chunks.jsonl")
    cases = [json.loads(line) for line in
             (root / "evaluation/rag_knowledge_smoke_cases.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    records = []
    for case in cases:
        start = time.perf_counter()
        hits = index.search(case["question"], args.top_k)
        retrieved = {hit["doc_id"] for hit in hits}
        gold = set(case["expected_doc_ids"])
        records.append({**case, "top_k": args.top_k,
                        "elapsed_seconds": round(time.perf_counter() - start, 4),
                        "document_recall": len(gold & retrieved) / len(gold) if gold else None,
                        "any_expected_document_hit": bool(gold & retrieved), "results": hits})
    eligible = [record for record in records if record["expected_route"] == "knowledge"]
    summary = {"scope": "development_smoke_document_level_only", "top_k": args.top_k,
               "case_count": len(records), "knowledge_case_count": len(eligible),
               "knowledge_document_recall_at_k": sum(r["document_recall"] for r in eligible) / len(eligible),
               "knowledge_hit_rate_at_k": sum(r["any_expected_document_hit"] for r in eligible) / len(eligible),
               "mean_retrieval_seconds": sum(r["elapsed_seconds"] for r in records) / len(records)}
    destination = root / "data/rag" / f"retrieval_smoke_top{args.top_k}.json"
    destination.write_text(json.dumps({"summary": summary, "cases": records}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({**summary, "report": str(destination)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
