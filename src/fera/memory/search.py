from __future__ import annotations

from fera.memory.expander import QueryExpander
from fera.memory.reranker import Reranker
from fera.memory.types import SearchResult

__all__ = ["SearchResult", "hybrid_search", "rrf_fuse", "deep_search"]

STOP_WORDS = frozenset(
    "the a an is are was were in on at to for of and or but not with this that it from by as".split()
)

RRF_K = 60


def rrf_fuse(ranked_lists: list[list[str]], *, k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion: merge ranked ID lists into {id: score}.

    score(doc) = sum(1 / (k + rank)) across all lists where doc appears.
    rank is 1-based.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _extract_keywords(query: str) -> list[str]:
    """Extract search keywords, removing stop words."""
    words = query.lower().split()
    return [w for w in words if w not in STOP_WORDS and len(w) > 1]


def _fts_search(conn, query: str, limit: int) -> list[str]:
    """Run FTS5 search, return ordered chunk IDs (best first)."""
    keywords = _extract_keywords(query)
    if not keywords:
        keywords = query.lower().split()
    if not keywords:
        return []

    fts_query = " AND ".join(f'"{kw}"' for kw in keywords)
    rows = conn.execute(
        """
        SELECT id FROM chunks_fts
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (fts_query, limit),
    ).fetchall()
    return [row[0] for row in rows]


def _vector_search(conn, embedder, query: str, limit: int) -> list[str]:
    """Run vector similarity search, return ordered chunk IDs (best first)."""
    query_vec = embedder.embed(query)
    rows = conn.execute(
        """
        SELECT id FROM chunks_vec
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
        """,
        (query_vec.tobytes(), limit),
    ).fetchall()
    return [row[0] for row in rows]


def _fetch_results(conn, scored_ids: list[tuple[str, float]]) -> list[SearchResult]:
    """Fetch chunk details and build SearchResult list for ranked (id, score) pairs."""
    if not scored_ids:
        return []
    chunk_ids = [cid for cid, _ in scored_ids]
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT id, path, start_line, end_line, text FROM chunks WHERE id IN ({placeholders})",
        chunk_ids,
    ).fetchall()
    chunk_map = {row[0]: row for row in rows}
    results = []
    for cid, score in scored_ids:
        if cid not in chunk_map:
            continue
        row = chunk_map[cid]
        results.append(
            SearchResult(
                path=row[1],
                start_line=row[2],
                end_line=row[3],
                score=score,
                snippet=row[4][:700],
            )
        )
    return results


def hybrid_search(
    index,
    query: str,
    *,
    max_results: int = 6,
    mode: str = "hybrid",
) -> list[SearchResult]:
    """Search memory chunks using hybrid FTS + vector search with RRF fusion."""
    candidates = min(200, max(1, max_results * 4))
    conn = index.conn

    ranked_lists: list[list[str]] = []

    if mode in ("hybrid", "fts"):
        fts_ids = _fts_search(conn, query, candidates)
        if fts_ids:
            ranked_lists.append(fts_ids)

    if mode in ("hybrid", "vector"):
        vec_ids = _vector_search(conn, index.embedder, query, candidates)
        if vec_ids:
            ranked_lists.append(vec_ids)

    scores = rrf_fuse(ranked_lists)
    if not scores:
        return []

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:max_results]
    return _fetch_results(conn, top)


async def deep_search(
    index,
    query: str,
    *,
    expander: QueryExpander,
    reranker: Reranker,
    max_results: int = 6,
    rerank_candidates: int = 20,
) -> list[SearchResult]:
    """Full search pipeline: expand query, retrieve, RRF fuse, rerank."""
    conn = index.conn
    candidates_per_query = min(200, max(1, rerank_candidates * 2))

    # Step 1: Expand query
    expansions = await expander.expand(query)
    all_queries = [query] + expansions

    # Step 2: Retrieve from all query variants
    ranked_lists: list[list[str]] = []
    for q in all_queries:
        fts_ids = _fts_search(conn, q, candidates_per_query)
        if fts_ids:
            ranked_lists.append(fts_ids)
        vec_ids = _vector_search(conn, index.embedder, q, candidates_per_query)
        if vec_ids:
            ranked_lists.append(vec_ids)

    # Step 3: RRF fusion
    scores = rrf_fuse(ranked_lists)
    if not scores:
        return []

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:rerank_candidates]
    candidates_for_rerank = _fetch_results(conn, top)

    # Step 4: Rerank
    reranked = await reranker.rerank(query, candidates_for_rerank)
    return reranked[:max_results]
