"""The site-wide Q&A guard sums a day of report_event model_call rows per question: index it
(REVIEW B-09; chat_event got the same index in 0009).

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade() -> None:
    op.execute("CREATE INDEX report_event_kind_at_idx ON report_event (kind, at)")


def downgrade() -> None:
    op.execute("DROP INDEX report_event_kind_at_idx")
