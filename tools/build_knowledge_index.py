import argparse
import json
import time
from pathlib import Path

from backend.services.rag.embeddings import APIEmbedder, EmbeddingSettings
from backend.services.rag.vector_index import build_index


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Embed chunks and publish a persistent FAISS index")
    parser.add_argument("--chunks", type=Path, default=root / "data/rag/chunks.jsonl")
    parser.add_argument("--index-dir", type=Path, default=root / "data/rag/index")
    args = parser.parse_args()
    started = time.perf_counter()
    manifest = build_index(args.chunks, args.index_dir, APIEmbedder(EmbeddingSettings.from_env()))
    print(json.dumps({"chunk_count": manifest["chunk_count"], "dimension": manifest["dimension"],
                      "model": manifest["embedding"]["model"], "index_dir": str(args.index_dir),
                      "elapsed_seconds": round(time.perf_counter() - started, 3)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
