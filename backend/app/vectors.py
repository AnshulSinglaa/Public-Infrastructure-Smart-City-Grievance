"""Vector store: Qdrant. Postgres keeps the rows; Qdrant keeps only vector + filter fields.

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

CHUNKS = "rule_chunks"  # bge-m3 text embeddings
PHOTOS = "photos"       # CLIP ViT-B/32 image embeddings

# collection -> (vector size, payload fields we filter on)
COLLECTIONS = {
    CHUNKS: (1024, {"lang": PayloadSchemaType.KEYWORD, "doc_id": PayloadSchemaType.INTEGER}),
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


def search_chunks(vector: list[float], k: int = 5, filters: dict | None = None) -> list:
    return search(CHUNKS, vector, k, filters)


def upsert_photo(media_id, vector: list[float], payload: dict) -> None:
    """payload: category, ward_no, master_id."""
    upsert(PHOTOS, str(media_id), vector, payload)


def search_photos(vector: list[float], k: int = 5, filters: dict | None = None) -> list:
    return search(PHOTOS, vector, k, filters)
