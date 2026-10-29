"""English-only letter (UX §14): complaint_hi becomes optional (kept for a later
"Translate" action), and the dossier keeps the citizen's own masked words.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute("ALTER TABLE dossier ALTER COLUMN complaint_hi DROP NOT NULL")
    op.execute("ALTER TABLE dossier ADD COLUMN citizen_text text")  # PII-masked, as written


def downgrade() -> None:
    op.execute("ALTER TABLE dossier DROP COLUMN citizen_text")
    op.execute("UPDATE dossier SET complaint_hi = '' WHERE complaint_hi IS NULL")
    op.execute("ALTER TABLE dossier ALTER COLUMN complaint_hi SET NOT NULL")
