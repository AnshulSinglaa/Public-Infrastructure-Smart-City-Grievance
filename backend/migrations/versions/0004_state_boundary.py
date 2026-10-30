"""Sprint 4: state boundaries (outside-coverage directory) + LangGraph checkpoint tables.

state_boundary is loaded by scripts/load_states.py from data/states.geojson.
The checkpoint tables belong to langgraph-checkpoint-postgres; its own setup() creates
them (and is safe to run twice), so we call it here instead of copying its SQL.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"

CHECKPOINT_TABLES = ["checkpoint_writes", "checkpoint_blobs", "checkpoints",
                     "checkpoint_migrations"]


def upgrade() -> None:
    op.execute("""
        CREATE TABLE state_boundary (
            code text PRIMARY KEY,
            name text NOT NULL,
            geom geometry(MultiPolygon, 4326) NOT NULL
        );
        CREATE INDEX state_boundary_geom_idx ON state_boundary USING gist (geom);
        ALTER TABLE state_boundary ENABLE ROW LEVEL SECURITY;
    """)
    from langgraph.checkpoint.postgres import PostgresSaver

    from app.config import settings

    with PostgresSaver.from_conn_string(settings.database_url) as saver:
        saver.setup()


def downgrade() -> None:
    for t in CHECKPOINT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t}")
    op.execute("DROP TABLE IF EXISTS state_boundary")
