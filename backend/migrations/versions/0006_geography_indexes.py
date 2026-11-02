"""Geography GIST indexes on ward and special_zone (REVIEW-S5 C2).

find_ward, discom and ifc_drain search with ST_DWithin(geom::geography, ...), which only
uses an index built on geom::geography (0001's indexes are on plain geom).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.execute("CREATE INDEX ward_geog_idx ON ward USING gist ((geom::geography))")
    op.execute("CREATE INDEX special_zone_geog_idx ON special_zone USING gist ((geom::geography))")


def downgrade() -> None:
    op.execute("DROP INDEX special_zone_geog_idx")
    op.execute("DROP INDEX ward_geog_idx")
