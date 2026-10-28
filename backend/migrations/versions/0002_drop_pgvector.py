"""Vectors moved to Qdrant (ARCHITECTURE §0): drop the pgvector columns and their indexes.

Dropping a column also drops its index; we drop the indexes first anyway so it reads clearly.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunk_embedding_idx")
    op.execute("DROP INDEX IF EXISTS media_clip_idx")
    op.execute("ALTER TABLE chunk DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS clip")
    # The `vector` extension stays installed (unused): 0001 needs it, so downgrade keeps working.


def downgrade() -> None:
    op.execute("ALTER TABLE chunk ADD COLUMN embedding vector(1024)")
    op.execute("ALTER TABLE media ADD COLUMN clip vector(512)")
    op.execute("CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX media_clip_idx ON media USING hnsw (clip vector_cosine_ops)")
