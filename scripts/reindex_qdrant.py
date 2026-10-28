"""Rebuild Qdrant from Postgres (Postgres is the source of truth, Qdrant is just an index).

Use it when the Qdrant Cloud free-tier cluster was paused/wiped after a week idle,
or after `docker volume rm` locally. It re-embeds every chunk and photo row and upserts it
with point id = the Postgres row id, so running it twice is harmless.

Run from the repo root:  uv run python scripts/reindex_qdrant.py
Uses DATABASE_URL, QDRANT_URL and QDRANT_API_KEY from .env.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import vectors
from app.config import settings
from app.db import connect

CHUNKS_SQL = "SELECT id, doc_id, section_ref, page, lang, text FROM chunk"
PHOTOS_SQL = """
SELECT m.id, m.storage_key, r.category, w.ward_no, r.master_id
FROM media m
JOIN report r ON r.media_id = m.id
LEFT JOIN master_issue mi ON mi.id = r.master_id
LEFT JOIN ward w ON w.id = mi.ward_id
WHERE m.kind = 'photo'
"""


# ponytail: the embedders land with the RAG/dedup sprint; until then these tables are empty.
def embed_text(text: str) -> list[float]:
    raise NotImplementedError("bge-m3 embedder not wired yet")


def embed_image(path: Path) -> list[float]:
    raise NotImplementedError("CLIP embedder not wired yet")


def main() -> None:
    vectors.ensure_collections()
    with connect() as conn:
        chunks = conn.execute(CHUNKS_SQL).fetchall()
        photos = conn.execute(PHOTOS_SQL).fetchall()

    try:
        for cid, doc_id, section_ref, page, lang, text in chunks:
            payload = {"doc_id": doc_id, "section_ref": section_ref, "page": page, "lang": lang}
            vectors.upsert_chunk(cid, embed_text(text), payload)
    except NotImplementedError as e:
        print(f"skipped {len(chunks)} chunks: {e}")

    try:
        for mid, storage_key, category, ward_no, master_id in photos:
            payload = {"category": category, "ward_no": ward_no,
                       "master_id": str(master_id) if master_id else None}
            vectors.upsert_photo(mid, embed_image(settings.media_dir / storage_key), payload)
    except NotImplementedError as e:
        print(f"skipped {len(photos)} photos: {e}")

    print(f"done: read {len(chunks)} chunks and {len(photos)} photos; Qdrant at {settings.qdrant_url}")


if __name__ == "__main__":
    main()
