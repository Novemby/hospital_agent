import argparse
import json
import time
from pathlib import Path

from backend.services.rag.embeddings import APIEmbedder, EmbeddingSettings
from backend.services.rag.vector_index import KnowledgeIndex


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Retrieve hospital knowledge with sources; does not generate answers")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--index-dir", type=Path, default=root / "data/rag/index")
    parser.add_argument("--chunks", type=Path, default=root / "data/rag/chunks.jsonl")
    args = parser.parse_args()
    started = time.perf_counter()
    index = KnowledgeIndex(args.index_dir, APIEmbedder(EmbeddingSettings.from_env()), args.chunks)
    results = index.search(args.query, args.top_k)
    print(json.dumps({"query": args.query, "elapsed_seconds": round(time.perf_counter() - started, 3),
                      "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
