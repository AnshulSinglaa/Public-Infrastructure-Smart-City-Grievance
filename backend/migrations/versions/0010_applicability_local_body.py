"""Sprint 6 fixes (FIX-PLAN-S6 B, C; B-071): who a document applies to, and India's local bodies.

- policy_doc.level / covers / not_for: from sources.yaml, also in the Qdrant payload, so retrieval
  never offers e.g. CPGRAMS (central) for a town drain (local). superseded_note: the issuing body
  no longer exists (SDMC -> MCD, 2022), so answers can say so.
- local_body: urban local bodies from the official Local Government Directory (lgdirectory.gov.in),
  loaded by scripts/load_local_bodies.py. Lookup: app/rag/local_body.py.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"

UP = """
ALTER TABLE policy_doc
    ADD COLUMN level text CHECK (level IN ('central', 'state', 'local')),
    ADD COLUMN covers text[] NOT NULL DEFAULT '{}',
    ADD COLUMN not_for text[] NOT NULL DEFAULT '{}',
    ADD COLUMN superseded_note text;

CREATE TABLE local_body (
    lgd_code int PRIMARY KEY,            -- LGD local body code
    name text NOT NULL,                  -- English name, e.g. 'Badhra'
    name_local text,
    body_type text NOT NULL,             -- e.g. 'Municipal Committee', 'Nagar Panchayat'
    district text,
    state_name text NOT NULL,
    state_code text,                     -- state_directory.csv code (HR)
    name_norm text NOT NULL              -- lower case, type words removed: lookup key
);
CREATE INDEX local_body_state_name_idx ON local_body (state_code, name_norm);
ALTER TABLE local_body ENABLE ROW LEVEL SECURITY;
"""

DOWN = """
DROP TABLE local_body;
ALTER TABLE policy_doc DROP COLUMN level, DROP COLUMN covers, DROP COLUMN not_for,
    DROP COLUMN superseded_note;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
