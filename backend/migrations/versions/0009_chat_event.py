"""Sprint 6b: chat_event, the home-page civic assistant's transcript (contract sprint6-api.md §4A).

Same shape as report_event, but per user (the home chat has no report). Kinds: chat (user),
chat_answer (assistant), model_call (hidden, counts toward the shared question budget),
chat_feedback. Payloads hold masked text only. Deleted with the user (ON DELETE CASCADE).

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE chat_event (
            id bigserial PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN ('user', 'assistant')),
            kind text NOT NULL,                 -- chat | chat_answer | model_call | chat_feedback
            payload jsonb NOT NULL DEFAULT '{}',
            at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX chat_event_user_idx ON chat_event (user_id, id)")
    op.execute("CREATE INDEX chat_event_kind_at_idx ON chat_event (kind, at)")
    op.execute("ALTER TABLE chat_event ENABLE ROW LEVEL SECURITY")  # as 0007: no anon access


def downgrade() -> None:
    op.execute("DROP TABLE chat_event")
