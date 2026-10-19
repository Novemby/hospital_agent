"""Run from project root: python -m tools.build_knowledge_chunks."""

import argparse
import json
from pathlib import Path

from backend.services.rag.documents import build_chunks, load_documents


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Export hospital Markdown knowledge to chunk JSONL")
    parser.add_argument("--knowledge-dir", type=Path, default=root / "knowledge/hospital")
    parser.add_argument("--output", type=Path, default=root / "data/rag/chunks.jsonl")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    args = parser.parse_args()
    documents = load_documents(args.knowledge_dir)
    chunks = build_chunks(documents, args.chunk_size, args.chunk_overlap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for chunk in chunks:
            stream.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    temporary.replace(args.output)
    for doc in documents:
        count = sum(c["doc_id"] == doc.metadata["doc_id"] for c in chunks)
        print(f"{doc.metadata['doc_id']}: {len(doc.body)} chars -> {count} chunks")
    print(f"Loaded {len(documents)} documents; exported {len(chunks)} chunks to {args.output}")


if __name__ == "__main__":
    main()
