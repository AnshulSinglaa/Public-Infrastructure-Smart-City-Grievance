"""Vector store: Qdrant. Postgres keeps the rows; Qdrant keeps only vector + filter fields.

rule_chunks (Sprint 6) is an alias -> rule_chunks_v2 with NAMED vectors: `dense` (768-d
gemini-embedding-001, cosine) and sparse `bm25` (fastembed Qdrant/bm25, IDF). The ingest job
creates it (app/rag/ingest.py ensure_collection) and Q&A searches it (app/rag/retrieve.py);
the API never creates it. The helpers below serve the unnamed-vector `photos` collection.

Point id = the Postgres row id, so a search hit joins straight back to its row:
`chunk.id` (an int) for rule_chunks, `str(media.id)` (a UUID string) for photos.
Qdrant accepts exactly these two id kinds.
"""

from functools import cache

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.config import settings

CHUNKS = "rule_chunks"  # alias owned by app/rag/ingest.py (named dense + bm25 vectors)
PHOTOS = "photos"       # CLIP ViT-B/32 image embeddings

# collections the API creates at startup -> (vector size, payload fields we filter on)
COLLECTIONS = {
    PHOTOS: (512, {"category": PayloadSchemaType.KEYWORD, "ward_no": PayloadSchemaType.INTEGER}),
}


@cache
def client() -> QdrantClient:
    """One shared client. The API key is only set for Qdrant Cloud."""
    key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return QdrantClient(url=settings.qdrant_url, api_key=key or None, timeout=5)


def create_collection(name: str, size: int, indexes: dict) -> None:
    """Create one cosine collection plus its payload indexes, if it is not there yet."""
    if client().collection_exists(name):
        return
    client().create_collection(name, vectors_config=VectorParams(size=size, distance=Distance.COSINE))
    for field, kind in indexes.items():
        client().create_payload_index(name, field, field_schema=kind)


def ensure_collections() -> None:
    for name, (size, indexes) in COLLECTIONS.items():
        create_collection(name, size, indexes)


def to_filter(filters: dict | None) -> Filter | None:
    """{"lang": "hi", "doc_id": 3} -> every field must match exactly (AND)."""
    if not filters:
        return None
    return Filter(must=[FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filters.items()])


def upsert(collection: str, point_id: int | str, vector: list[float], payload: dict) -> None:
    client().upsert(collection, [PointStruct(id=point_id, vector=vector, payload=payload)])


def search(collection: str, vector: list[float], k: int = 5, filters: dict | None = None) -> list:
    """Nearest k points (best first). Each hit has .id, .score (cosine) and .payload."""
    return client().query_points(collection, query=vector, limit=k,
                                 query_filter=to_filter(filters)).points


def upsert_chunk(chunk_id: int, vector: list[float], payload: dict) -> None:
    """payload: doc_id, section_ref, page, lang (the text stays in Postgres)."""
    upsert(CHUNKS, chunk_id, vector, payload)


def upsert_photo(media_id, vector: list[float], payload: dict) -> None:
    """payload: category, ward_no, master_id."""
    upsert(PHOTOS, str(media_id), vector, payload)


def search_photos(vector: list[float], k: int = 5, filters: dict | None = None) -> list:
    return search(PHOTOS, vector, k, filters)
