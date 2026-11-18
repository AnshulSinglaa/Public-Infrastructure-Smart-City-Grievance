"""Retrieval for Q&A (HLD §4): Qdrant dense + BM25 -> RRF -> MiniLM rerank -> top 4 chunks.

    masked question --> Gemini embed (dense) --+
                    --> fastembed BM25 (sparse) +--> Qdrant: prefetch 20 + 20 (+ 20 for the
                                                     report's department) -> RRF -> 20
    --> rerank (question, chunk) with ms-marco-MiniLM-L-6-v2, unless the question is in
        Devanagari (English-only model) or RERANK=off --> top 4 --> text + metadata from
        Postgres, each trimmed around its best-matching sentences (~1.5k-token prompt)

Degraded, never broken: no embedding -> BM25 only; Qdrant down -> Postgres full-text search.
Layers: national always; delhi only for Delhi; state only for that state. Postgres checks the
layer, approval, tier and validity again, so a wrong Qdrant payload can't leak a Delhi rule
into an answer for Jaipur.
"""

import logging
import re
from functools import cache

from qdrant_client import models as qm

from app import vectors
from app.config import settings
from app.llm import gateway
from app.privacy.text import WORDCHAR
from app.providers import fakes
from app.providers.common import ProviderDown
from app.rag.ingest import normalise

log = logging.getLogger(__name__)
CANDIDATES, TOP_K = 20, 4
TRIM_CHARS = 700  # ~175 tokens per chunk in the prompt
DEVANAGARI = re.compile("[ऀ-ॿ]")
STALE_DAYS = 90


@cache
def bm25():
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding("Qdrant/bm25")


@cache
def reranker():
    """~80 MB ONNX file, loaded on the first question (not at startup)."""
    if settings.provider_mode == "fake":
        return fakes.Reranker()
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder("Xenova/ms-marco-MiniLM-L-6-v2")


def sparse(question: str) -> qm.SparseVector:
    if settings.provider_mode == "fake":
        indices, values = fakes.sparse(question)
    else:
        e = next(iter(bm25().query_embed(question)))
        indices, values = e.indices.tolist(), e.values.tolist()
    return qm.SparseVector(indices=indices, values=values)


def layer_filter(delhi: bool, state_code: str | None) -> qm.Filter:
    allowed = [qm.FieldCondition(key="layer", match=qm.MatchValue(value="national"))]
    if delhi:
        allowed.append(qm.FieldCondition(key="layer", match=qm.MatchValue(value="delhi")))
    elif state_code:
        allowed.append(qm.Filter(must=[
            qm.FieldCondition(key="layer", match=qm.MatchValue(value="state")),
            qm.FieldCondition(key="state_code", match=qm.MatchValue(value=state_code))]))
    return qm.Filter(
        must=[qm.FieldCondition(key="status", match=qm.MatchValue(value="approved")),
              qm.FieldCondition(key="tier", match=qm.MatchAny(any=[1, 2]))],
        should=allowed)


def qdrant_ids(question: str, delhi: bool, state_code: str | None,
               department: str | None, scope: str) -> tuple[list[int], bool]:
    """Candidate chunk ids, best first (RRF), and whether the dense vector was missing."""
    question = normalise(question)  # the same text form the ingest job embedded
    where = layer_filter(delhi, state_code)
    if scope == "local_body_matters":  # central documents can't answer a local issue (B-065)
        where.must_not = [qm.FieldCondition(key="level", match=qm.MatchValue(value="central"))]
    prefetch = [qm.Prefetch(query=sparse(question), using="bm25", limit=CANDIDATES,
                            filter=where)]
    degraded = False
    try:
        dense = list(gateway.embed_query(question))
        prefetch.append(qm.Prefetch(query=dense, using="dense", limit=CANDIDATES, filter=where))
        if department:  # a soft boost, not a hard filter (HLD §4)
            boost = qm.Filter(must=[*where.must, qm.FieldCondition(
                key="department_codes", match=qm.MatchValue(value=department))],
                should=where.should, must_not=where.must_not)
            prefetch.append(qm.Prefetch(query=dense, using="dense", limit=CANDIDATES,
                                        filter=boost))
    except ProviderDown:
        degraded = True  # keyword search only
    hits = vectors.client().query_points(
        settings.chunks_collection, prefetch=prefetch, limit=CANDIDATES,
        query=qm.FusionQuery(fusion=qm.Fusion.RRF)).points
    return [int(h.id) for h in hits], degraded


ROW_SQL = """
SELECT c.id, c.text, c.section_ref, c.page, coalesce(d.title, d.key) AS title, d.issuer,
       d.doc_type, d.tier, d.layer, d.level, d.issued_on, d.url, v.fetched_at,
       d.status = 'dead' AS broken, d.superseded_note,
       coalesce(d.last_ok_at, v.fetched_at) < now() - make_interval(days => %(stale)s) AS stale
FROM chunk c JOIN policy_doc d ON d.id = c.doc_id
LEFT JOIN doc_version v ON v.id = c.doc_version_id
WHERE c.status = 'approved' AND NOT c.injection_flag AND d.tier IN (1, 2)
  AND d.status IN ('active', 'dead')  -- a retired source is never quoted (REVIEW R-03)
  AND (v.id IS NULL OR (v.status = 'approved' AND (v.valid_to IS NULL OR v.valid_to >= now())))
  -- applicability (B-065): never a document that says it is not for this kind of issue, and
  -- no central document for a local issue unless it covers it
  AND NOT (d.not_for && ARRAY[%(scope)s]::text[])
  AND (%(scope)s <> 'local_body_matters' OR d.level IS DISTINCT FROM 'central'
       OR d.covers && ARRAY[%(scope)s]::text[])
  AND (d.layer = 'national' OR (d.layer = 'delhi' AND %(delhi)s)
       OR (d.layer = 'state' AND d.state_code = %(state)s))
"""


def rows(conn, sql_tail: str, params: dict) -> list[dict]:
    cur = conn.execute(ROW_SQL + sql_tail, params)
    names = [c.name for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def keyword_rows(conn, question: str, params: dict) -> list[dict]:
    """Fallback when Qdrant is down: Postgres full-text, any of the question's words."""
    words = re.findall(rf"{WORDCHAR}+", question.casefold())[:30]
    if not words:
        return []
    return rows(conn, " AND c.tsv @@ to_tsquery('simple', %(q)s)"
                " ORDER BY ts_rank(c.tsv, to_tsquery('simple', %(q)s)) DESC LIMIT %(k)s",
                params | {"q": " | ".join(words), "k": CANDIDATES})


SENTENCE = re.compile(r"(?<=[.?!।;])\s+|\n+")


def trim(text: str, question: str, limit: int = TRIM_CHARS) -> str:
    """The part of a chunk around its sentences that share the most words with the question,
    at most `limit` characters, so 4 chunks fit a ~1.5k-token prompt (B-056)."""
    if len(text) <= limit:
        return text
    parts = [p for p in SENTENCE.split(text) if p.strip()]
    q = set(re.findall(rf"{WORDCHAR}+", question.casefold()))
    best = max(range(len(parts)),
               key=lambda i: len(q & set(re.findall(rf"{WORDCHAR}+", parts[i].casefold()))))
    lo = hi = best
    size = len(parts[best])
    while True:  # grow the window around the best sentence, one sentence at a time
        grew = False
        for j in (hi + 1, lo - 1):
            if 0 <= j < len(parts) and (j > hi or j < lo) and size + len(parts[j]) + 1 <= limit:
                size += len(parts[j]) + 1
                lo, hi, grew = min(lo, j), max(hi, j), True
        if not grew:
            break
    return " ".join(parts[lo:hi + 1])[:limit]


def search(conn, question: str, *, delhi: bool, state_code: str | None,
           department: str | None = None, scope: str = "local_body_matters") -> dict:
    """{"chunks": [row, ...] (best first, at most 4, each with `excerpt`), "degraded",
    "reranked"}. `question` must be MASKED (it is embedded by Gemini)."""
    params = {"delhi": delhi, "state": state_code, "stale": STALE_DAYS, "scope": scope}
    try:
        with conn.transaction():  # a savepoint: a failed query here can't break the fallback
            ids, degraded = qdrant_ids(question, delhi, state_code, department, scope)
            found = {r["id"]: r for r in rows(conn, " AND c.id = ANY(%(ids)s)",
                                              params | {"ids": ids})}
        candidates = [found[i] for i in ids if i in found]
    except Exception as e:  # noqa: BLE001 - Qdrant down / timeout: keyword search instead
        log.warning("Qdrant search failed (%s); using Postgres full-text", type(e).__name__)
        candidates, degraded = keyword_rows(conn, question, params), True
    reranked = False
    if settings.rerank and len(candidates) > 1 and not DEVANAGARI.search(question):
        try:
            scores = list(reranker().rerank(question, [c["text"] for c in candidates]))
            order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
            candidates, reranked = [candidates[i] for i in order], True
        except Exception as e:  # noqa: BLE001 - out of memory, missing model file: keep RRF
            log.warning("reranker failed (%s); keeping the RRF order", type(e).__name__)
    chunks = [c | {"excerpt": trim(c["text"], question)} for c in candidates[:TOP_K]]
    return {"chunks": chunks, "degraded": degraded, "reranked": reranked}
