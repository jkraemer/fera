from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder:
    """Thin wrapper around fastembed for local ONNX-based embeddings."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self._model = TextEmbedding(model_name=model_name)

    @property
    def dimension(self) -> int:
        return 384  # all-MiniLM-L6-v2 output dimension

    def embed(self, text: str) -> np.ndarray:
        vecs = list(self._model.embed([text]))
        return vecs[0].astype(np.float32)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        vecs = list(self._model.embed(texts))
        return np.array(vecs, dtype=np.float32)
