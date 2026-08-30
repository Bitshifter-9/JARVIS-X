"""Embeddings, computed on the VPS.

fastembed rather than sentence-transformers: same MiniLM weights, ONNX runtime instead of
torch, so the install is tens of megabytes rather than gigabytes on a free-tier VM. 384
dimensions, no API call, no rate limit, and the corpus never leaves our machine.

``HashEmbedder`` is the deterministic stand-in used by tests, so the suite does not
download a model.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

DIMENSIONS = 384


class Embedder(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    dimensions = DIMENSIONS
    model_name = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._load().embed(texts)]


class HashEmbedder:
    """Deterministic hashed bag-of-words. Not semantic, but stable and free.

    Good enough to exercise the retrieval path end to end; useless for actual similarity,
    which is why it is never the default outside tests.
    """

    dimensions = DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


_default: Embedder | None = None


def get_embedder() -> Embedder:
    global _default
    if _default is None:
        _default = FastEmbedder()
    return _default


def set_embedder(embedder: Embedder) -> None:
    global _default
    _default = embedder
