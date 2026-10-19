"""OpenAI-compatible Embedding API, configured separately from chat models."""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urlsplit

from backend.config import _load_local_env


@dataclass(frozen=True)
class EmbeddingSettings:
    model: str
    base_url: str
    api_key: str = field(repr=False)
    batch_size: int = 16
    timeout: float = 30
    max_retries: int = 2

    @classmethod
    def from_env(cls):
        _load_local_env()
        config = cls(
            model=os.getenv("RAG_EMBEDDING_MODEL", "").strip(),
            base_url=os.getenv("RAG_EMBEDDING_BASE_URL", "").strip().rstrip("/"),
            api_key=os.getenv("RAG_EMBEDDING_API_KEY", "").strip(),
            batch_size=int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "16")),
            timeout=float(os.getenv("RAG_EMBEDDING_TIMEOUT", "30")),
            max_retries=int(os.getenv("RAG_EMBEDDING_MAX_RETRIES", "2")),
        )
        if not config.model or not config.base_url or not config.api_key:
            raise ValueError("Set RAG_EMBEDDING_MODEL, RAG_EMBEDDING_BASE_URL and RAG_EMBEDDING_API_KEY in .env.local")
        url = urlsplit(config.base_url)
        if url.scheme not in ("https", "http") or not url.netloc or url.query or url.fragment or url.username:
            raise ValueError("Embedding Base URL must be an HTTP(S) URL without credentials/query/fragment")
        if config.batch_size < 1 or config.timeout <= 0 or not 0 <= config.max_retries <= 5:
            raise ValueError("Invalid Embedding batch size, timeout or retry count")
        return config


class APIEmbedder:
    def __init__(self, settings: EmbeddingSettings):
        self.settings = settings

    @property
    def signature(self):
        # Never persist credentials. Input formatting must match at query time.
        return {"provider": "openai_compatible", "model": self.settings.model,
                "base_url": self.settings.base_url, "document_format": "title_section_text_v1"}

    def _request(self, texts: List[str]):
        request = urllib.request.Request(
            self.settings.base_url + "/embeddings",
            data=json.dumps({"model": self.settings.model, "input": texts,
                             "encoding_format": "float"}).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.settings.api_key}, method="POST")
        for attempt in range(self.settings.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.settings.timeout) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                if status not in (408, 429, 500, 502, 503, 504) or attempt == self.settings.max_retries:
                    # Provider error bodies can contain submitted content or credentials.
                    raise RuntimeError(f"Embedding API HTTP {status}; check configuration/quota") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.settings.max_retries:
                    raise RuntimeError("Embedding API connection failed or timed out") from None
            time.sleep(min(2 ** attempt, 4))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("Embedding API returned an unexpected number of vectors")
        if any(not isinstance(item, dict) or type(item.get("index")) is not int for item in data):
            raise ValueError("Embedding API returned invalid vector indices")
        ordered = sorted(data, key=lambda item: item["index"])
        if [item["index"] for item in ordered] != list(range(len(texts))):
            raise ValueError("Embedding API returned duplicate/missing vector indices")
        return [item.get("embedding") for item in ordered]

    def embed_documents(self, texts: List[str]):
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be nonempty")
        vectors = []
        for start in range(0, len(texts), self.settings.batch_size):
            vectors.extend(self._request(texts[start:start + self.settings.batch_size]))
        return vectors

    def embed_query(self, query: str):
        return self.embed_documents([query])[0]
