"""Sprint 6 corpus: sources, versions, chunks and rule citations (contract sprint6-api.md §2.3).

- policy_doc (exists since 0001) = one entry of data/corpus/sources.yaml, keyed by `key`.
- doc_version (new) = one fetched copy; a new cleaned-text hash makes a new row. Never deleted.
- chunk (exists) gains its version, position, hash, injection flag and review status.
- citation (exists) gains the rule it backs, its role and its review status.
Filled by scripts/ingest_corpus.py; citations approved with scripts/review_corpus.py.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"

UP = """
ALTER TABLE policy_doc
    ADD COLUMN key text UNIQUE,
    ADD COLUMN issuer text,
    ADD COLUMN doc_type text,
    ADD COLUMN tier int,
    ADD COLUMN layer text CHECK (layer IN ('national', 'state', 'delhi')),
    ADD COLUMN state_code text,
    ADD COLUMN issued_on date,
    ADD COLUMN department_codes text[] NOT NULL DEFAULT '{}',
    ADD COLUMN categories text[] NOT NULL DEFAULT '{}',
    ADD COLUMN rules text[] NOT NULL DEFAULT '{}',
    ADD COLUMN recrawl_days int NOT NULL DEFAULT 30,
    ADD COLUMN status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'dead', 'retired')),
    ADD COLUMN etag text,
    ADD COLUMN last_modified text,
    ADD COLUMN last_checked_at timestamptz,
    ADD COLUMN last_ok_at timestamptz,
    ADD COLUMN fail_count int NOT NULL DEFAULT 0,
    ADD COLUMN last_error text;            -- why the last fetch failed (http_404, needs_ocr, ...)

CREATE TABLE doc_version (
    id serial PRIMARY KEY,
    doc_id int NOT NULL REFERENCES policy_doc(id) ON DELETE CASCADE,
    version_no int NOT NULL,
    sha256 text NOT NULL,                  -- of the cleaned text
    fetched_at timestamptz NOT NULL DEFAULT now(),
    raw_key text,                          -- where the raw file is kept: corpus/<key>/<version>.<ext>
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,                  -- set when a newer version supersedes it
    ocr_pages int NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'approved', 'superseded', 'rejected')),
    reviewed_at timestamptz,
    UNIQUE (doc_id, version_no)
);
ALTER TABLE doc_version ENABLE ROW LEVEL SECURITY;

ALTER TABLE chunk
    ADD COLUMN doc_version_id int REFERENCES doc_version(id) ON DELETE CASCADE,
    ADD COLUMN ordinal int,
    ADD COLUMN text_sha text,
    ADD COLUMN injection_flag boolean NOT NULL DEFAULT false,
    ADD COLUMN status text NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'approved', 'superseded', 'rejected')),
    ADD CONSTRAINT chunk_version_ordinal_key UNIQUE (doc_version_id, ordinal);

ALTER TABLE citation
    ADD COLUMN rule_id text REFERENCES routing_rule(rule_id) ON DELETE CASCADE,
    ADD COLUMN role text NOT NULL DEFAULT 'ownership' CHECK (role IN ('ownership', 'deadline')),
    ADD COLUMN status text NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'needs_review', 'rejected')),
    ADD COLUMN reviewed_at timestamptz;
CREATE INDEX citation_rule_idx ON citation (rule_id, status);
"""

DOWN = """
DROP INDEX citation_rule_idx;
ALTER TABLE citation DROP COLUMN rule_id, DROP COLUMN role, DROP COLUMN status,
    DROP COLUMN reviewed_at;
ALTER TABLE chunk DROP CONSTRAINT chunk_version_ordinal_key, DROP COLUMN doc_version_id,
    DROP COLUMN ordinal, DROP COLUMN text_sha, DROP COLUMN injection_flag, DROP COLUMN status;
DROP TABLE doc_version;
ALTER TABLE policy_doc DROP COLUMN key, DROP COLUMN issuer, DROP COLUMN doc_type, DROP COLUMN tier,
    DROP COLUMN layer, DROP COLUMN state_code, DROP COLUMN issued_on, DROP COLUMN department_codes,
    DROP COLUMN categories, DROP COLUMN rules, DROP COLUMN recrawl_days, DROP COLUMN status,
    DROP COLUMN etag, DROP COLUMN last_modified, DROP COLUMN last_checked_at,
    DROP COLUMN last_ok_at, DROP COLUMN fail_count, DROP COLUMN last_error;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
