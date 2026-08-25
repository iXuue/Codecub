"""Optional OpenAI-compatible embeddings adapter."""

from __future__ import annotations

import json
import urllib.request
from typing import Protocol


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...


class OpenAICompatibleEmbeddingClient:
    def __init__(self, base_url, api_key, model, timeout=10):
        self.base_url, self.api_key, self.model, self.timeout = (
            base_url.rstrip("/"),
            api_key,
            model,
            timeout,
        )

    def embed(self, text):
        return self.embed_many([text])[0]

    def embed_many(self, texts):
        # The configured DashScope OpenAI-compatible endpoint accepts one text
        # per request.  Keep the public batch interface while preserving that
        # provider's contract.
        if len(texts) > 1:
            return [self.embed(text) for text in texts]
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps({"model": self.model, "input": texts[0]}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode())
        return [[float(value) for value in item["embedding"]] for item in data["data"]]
