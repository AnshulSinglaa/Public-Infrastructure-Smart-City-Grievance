"""Deploy: login attempts in Postgres (SEC-004) + row-level security on every table.

login_attempt: one row per login try, keyed by "ip:<ip>" or "otp:<email hash>". The limit
counts rows in the last 10 minutes, so it survives restarts and is shared by all workers.

RLS: Supabase also serves the public schema over its REST API to anyone holding the
(public) anon key. RLS on with no policies = that API sees nothing. Our API connects as
the table owner, which RLS does not apply to, so the app itself is unaffected.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"

# Every table in public except PostGIS's own (owned by the extension, not by us).
TABLES = """SELECT tablename FROM pg_tables WHERE schemaname = 'public'
            AND tablename <> 'spatial_ref_sys'"""


def upgrade() -> None:
    op.execute("""
        CREATE TABLE login_attempt (
            key text NOT NULL,
            at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX login_attempt_key_at_idx ON login_attempt (key, at)")
    op.execute(f"""DO $$ DECLARE t text; BEGIN
        FOR t IN {TABLES} LOOP
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        END LOOP; END $$""")


def downgrade() -> None:
    op.execute(f"""DO $$ DECLARE t text; BEGIN
        FOR t IN {TABLES} LOOP
            EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
        END LOOP; END $$""")
    op.execute("DROP TABLE login_attempt")
