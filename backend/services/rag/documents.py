"""Manifest-based Markdown loading and character-based, source-traceable chunking."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml


@dataclass
class KnowledgeDocument:
    metadata: Dict[str, Any]
    body: str
    source_path: str


def load_documents(knowledge_dir: Path) -> List[KnowledgeDocument]:
    """Load only manifest entries. Offsets use LF-normalized body characters."""
    root = Path(knowledge_dir).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    entries = manifest["documents"]
    if manifest["document_count"] != len(entries):
        raise ValueError("manifest document_count does not match documents")
    documents = []
    seen_ids, seen_paths = set(), set()
    required = ("doc_id", "title", "category", "hospital_id", "status", "version",
                "updated_at", "source_type")
    for entry in entries:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".md":
            raise ValueError(f"Invalid document path: {entry['path']}")
        if path in seen_paths or entry["doc_id"] in seen_ids:
            raise ValueError(f"Duplicate document: {entry['doc_id']}")
        raw = path.read_text(encoding="utf-8-sig")
        match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", raw, re.DOTALL)
        if not match:
            raise ValueError(f"Missing front matter: {path}")
        metadata = yaml.safe_load(match.group(1))
        if not isinstance(metadata, dict):
            raise ValueError(f"Invalid front matter: {path}")
        for key in required:
            if not isinstance(metadata.get(key), str) or not metadata[key].strip():
                raise ValueError(f"{path}: {key} must be a nonempty quoted string")
        refs = metadata.get("source_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError(f"{path}: source_refs must be a list of strings")
        for key in ("doc_id", "title", "category"):
            if metadata[key] != entry[key]:
                raise ValueError(f"{path}: {key} differs from manifest")
        for key in ("hospital_id", "status"):
            if metadata[key] != manifest[key]:
                raise ValueError(f"{path}: {key} differs from manifest")
        body = raw[match.end():]
        if not body.strip():
            raise ValueError(f"Empty document: {path}")
        documents.append(KnowledgeDocument(metadata, body, path.relative_to(root).as_posix()))
        seen_ids.add(entry["doc_id"])
        seen_paths.add(path)
    return documents


def _trim_span(body: str, start: int, end: int):
    while start < end and body[start].isspace():
        start += 1
    while end > start and body[end - 1].isspace():
        end -= 1
    return start, end


def chunk_document(document: KnowledgeDocument, chunk_size: int = 500,
                   chunk_overlap: int = 80) -> List[Dict[str, Any]]:
    """Keep short documents whole; otherwise never merge across H2 sections.

    Prefer paragraph/sentence endings. Overlap applies within a section and can
    be smaller than requested to avoid cutting paragraphs; it never crosses H2.
    start/end are half-open offsets into document.body, not raw Markdown files.
    """
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("Require chunk_size > 0 and 0 <= chunk_overlap < chunk_size")
    body = document.body
    headings = list(re.finditer(r"^##[ \t]+(.+?)[ \t]*$", body, re.MULTILINE))
    if len(body.strip()) <= chunk_size:
        sections = [(document.metadata["title"], 0, len(body))]
    else:
        sections = []
        if not headings:
            sections.append((document.metadata["title"], 0, len(body)))
        else:
            sections.append(("文档说明", 0, headings[0].start()))
            sections.extend((heading.group(1), heading.start(),
                             headings[i + 1].start() if i + 1 < len(headings) else len(body))
                            for i, heading in enumerate(headings))
    chunks = []
    for section, section_start, section_end in sections:
        start, limit = _trim_span(body, section_start, section_end)
        # Paragraph boundaries are safe split/overlap points.
        boundaries = [start + m.end() for m in re.finditer(r"\n[ \t]*\n", body[start:limit])]
        while start < limit:
            end = min(start + chunk_size, limit)
            if end < limit:
                candidates = [p for p in boundaries if start < p <= end]
                if candidates:
                    end = candidates[-1]
                else:
                    sentences = list(re.finditer(r"[。！？!?](?:[”’])?", body[start:end]))
                    if sentences:
                        end = start + sentences[-1].end()
            text_start, text_end = _trim_span(body, start, end)
            if text_start < text_end:
                content = body[text_start:text_end]
                identity = json.dumps([document.metadata["doc_id"], document.metadata["version"],
                                       text_start, text_end, content], ensure_ascii=False)
                digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
                chunks.append({**document.metadata, "source_path": document.source_path,
                               "section": section,
                               "chunk_id": f"{document.metadata['doc_id']}:{digest}",
                               "start_char": text_start, "end_char": text_end, "text": content})
            if end == limit:
                break
            next_start = max(start + 1, end - chunk_overlap)
            # When splitting at a paragraph boundary, reuse whole paragraphs only.
            if end in boundaries:
                safe = [p for p in boundaries if next_start <= p < end]
                next_start = safe[0] if safe else end
            start = next_start
    return chunks


def build_chunks(documents: List[KnowledgeDocument], chunk_size: int = 500,
                 chunk_overlap: int = 80) -> List[Dict[str, Any]]:
    return [chunk for doc in documents for chunk in chunk_document(doc, chunk_size, chunk_overlap)]
