"""Normalized FAISS exact search with versioned, atomic index publication."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np


def read_chunks(path):
    chunks, seen = [], set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        chunk = json.loads(line)
        for key in ("chunk_id", "doc_id", "title", "section", "text", "status", "source_path"):
            if not isinstance(chunk.get(key), str) or not chunk[key].strip():
                raise ValueError(f"Chunk line {line_number}: missing/invalid {key}")
        if chunk["chunk_id"] in seen:
            raise ValueError(f"Duplicate chunk ID at line {line_number}")
        seen.add(chunk["chunk_id"])
        chunks.append(chunk)
    if not chunks:
        raise ValueError("No chunks to index")
    return chunks


def normalized_vectors(values, rows, dimension=None):
    try:
        array = np.array(values, dtype="float32", order="C", copy=True)
    except (TypeError, ValueError):
        raise ValueError("Embedding vectors must be a rectangular numeric matrix") from None
    if array.ndim != 2 or array.shape[0] != rows or array.shape[1] < 1:
        raise ValueError("Embedding shape does not match input count")
    if dimension is not None and array.shape[1] != dimension:
        raise ValueError("Embedding dimension changed; rebuild index with the configured model")
    if not np.isfinite(array).all():
        raise ValueError("Embedding contains nonfinite values")
    norms = np.linalg.norm(array.astype("float64"), axis=1)
    if np.any(norms <= 0):
        raise ValueError("Embedding contains zero vectors")
    array = np.ascontiguousarray(array / norms[:, None], dtype="float32")
    return array


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def build_index(chunks_path, index_dir, embedder):
    chunks = read_chunks(chunks_path)
    texts = [f"{c['title']}\n{c['section']}\n{c['text']}" for c in chunks]
    vectors = normalized_vectors(embedder.embed_documents(texts), len(chunks))
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    root = Path(index_dir)
    root.mkdir(parents=True, exist_ok=True)
    generation = uuid.uuid4().hex
    folder = root / generation
    folder.mkdir()
    # serialize_index supports Unicode Windows paths via Python's file I/O.
    index_bytes = faiss.serialize_index(index).tobytes()
    metadata_bytes = json.dumps(chunks, ensure_ascii=False).encode("utf-8")
    (folder / "index.faiss").write_bytes(index_bytes)
    (folder / "chunks.json").write_bytes(metadata_bytes)
    manifest = {"schema_version": 1, "generation": generation,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "embedding": embedder.signature, "metric": "cosine",
                "dimension": int(vectors.shape[1]), "chunk_count": len(chunks),
                "source_sha256": _digest(Path(chunks_path).read_bytes()),
                "index_sha256": _digest(index_bytes), "metadata_sha256": _digest(metadata_bytes)}
    temporary = root / f"manifest-{generation}.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(root / "manifest.json")
    return manifest


class KnowledgeIndex:
    def __init__(self, index_dir, embedder, chunks_path=None):
        root = Path(index_dir)
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest = self.manifest
        if manifest["schema_version"] != 1 or manifest["metric"] != "cosine":
            raise ValueError("Unsupported index schema or metric")
        if manifest["embedding"] != embedder.signature:
            raise ValueError("Embedding model/endpoint/input format differs from index; rebuild index")
        generation = manifest["generation"]
        if not isinstance(generation, str) or len(generation) != 32 or any(c not in "0123456789abcdef" for c in generation):
            raise ValueError("Invalid index generation")
        if chunks_path is not None and _digest(Path(chunks_path).read_bytes()) != manifest["source_sha256"]:
            raise ValueError("Chunks changed after indexing; rebuild index")
        folder = root / generation
        index_bytes = (folder / "index.faiss").read_bytes()
        metadata_bytes = (folder / "chunks.json").read_bytes()
        if _digest(index_bytes) != manifest["index_sha256"] or _digest(metadata_bytes) != manifest["metadata_sha256"]:
            raise ValueError("Index/metadata checksum mismatch; rebuild index")
        self.index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype="uint8").copy())
        self.chunks = json.loads(metadata_bytes)
        if self.index.d != manifest["dimension"] or self.index.ntotal != len(self.chunks) or len(self.chunks) != manifest["chunk_count"]:
            raise ValueError("Index dimensions/count do not match metadata")
        self.embedder = embedder

    def search(self, query, top_k=3):
        if not query.strip() or type(top_k) is not int or top_k < 1:
            raise ValueError("Require a nonempty query and positive integer top_k")
        vector = normalized_vectors([self.embedder.embed_query(query)], 1, self.index.d)
        scores, positions = self.index.search(vector, min(top_k, len(self.chunks)))
        return [{**self.chunks[int(position)], "score": float(score)}
                for score, position in zip(scores[0], positions[0])]
