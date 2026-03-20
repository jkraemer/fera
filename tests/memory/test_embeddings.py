import numpy as np

from fera.memory.embeddings import Embedder

# Use a shared instance — model loading is expensive
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def test_embed_single_text():
    embedder = get_embedder()
    vec = embedder.embed("hello world")
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (384,)
    assert vec.dtype == np.float32


def test_embed_batch():
    embedder = get_embedder()
    vecs = embedder.embed_batch(["hello", "world", "foo"])
    assert isinstance(vecs, np.ndarray)
    assert vecs.shape == (3, 384)


def test_embeddings_are_normalized():
    embedder = get_embedder()
    vec = embedder.embed("test normalization")
    norm = np.linalg.norm(vec)
    assert abs(norm - 1.0) < 0.01


def test_similar_texts_have_high_similarity():
    embedder = get_embedder()
    v1 = embedder.embed("the cat sat on the mat")
    v2 = embedder.embed("a cat was sitting on a mat")
    v3 = embedder.embed("quantum physics research paper")
    sim_close = np.dot(v1, v2)
    sim_far = np.dot(v1, v3)
    assert sim_close > sim_far
