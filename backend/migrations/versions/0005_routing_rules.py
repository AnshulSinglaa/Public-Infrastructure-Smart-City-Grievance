"""Sprint 5: real routing data + chat events.

- routing_rule is rebuilt to mirror data/rules_draft.csv (key = rule_id, e.g. 'RD-03').
  The old table had a different shape and was never filled. The rule's *condition* stays
  as prose here; how each one is checked lives in app/routing/conditions.py.
- department gains the departments.csv columns; contact gets a unique key so the loader
  can upsert.
- road_segment gains the PWD-match and width fields.
- report_event stores every chat step (report, question, answer, outcome).
Filled by scripts/load_rules.py (data agent); the exact columns are in sprint5-api.md §5.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"

UP = """
DROP TABLE routing_rule CASCADE;  -- also drops the 3 FKs that pointed at its int id
ALTER TABLE master_issue ALTER COLUMN routing_rule_id TYPE text USING NULL;
ALTER TABLE routing_decision ALTER COLUMN rule_id TYPE text USING NULL;
ALTER TABLE outcome_feedback ALTER COLUMN routing_rule_id TYPE text USING NULL;

CREATE TABLE routing_rule (
    rule_id text PRIMARY KEY,            -- 'RD-03'
    priority int NOT NULL,               -- lower number = checked first
    categories text[] NOT NULL,          -- CSV 'category' split on ';' ({'*'} = any)
    condition text NOT NULL,             -- CSV prose, shown to people, not parsed
    department_code text NOT NULL,       -- departments.csv code
    sla_days int,
    sla_source_url text,
    rule_source_url text,                -- empty = no citation (confidence x 0.8)
    confidence real NOT NULL,
    notes text,
    dataset_version int REFERENCES dataset_version(id),
    last_verified date                   -- older than a year = stale (confidence x 0.9)
);
CREATE INDEX routing_rule_priority_idx ON routing_rule (priority);
ALTER TABLE routing_rule ENABLE ROW LEVEL SECURITY;

ALTER TABLE master_issue ADD FOREIGN KEY (routing_rule_id) REFERENCES routing_rule(rule_id);
ALTER TABLE routing_decision ADD FOREIGN KEY (rule_id) REFERENCES routing_rule(rule_id);
ALTER TABLE outcome_feedback ADD FOREIGN KEY (routing_rule_id) REFERENCES routing_rule(rule_id);

ALTER TABLE department
    ADD COLUMN level text, ADD COLUMN owns text, ADD COLUMN jurisdiction_type text,
    ADD COLUMN helpline text, ADD COLUMN grievance_url text, ADD COLUMN email text,
    ADD COLUMN source_url text, ADD COLUMN accessed date, ADD COLUMN verified text;
CREATE UNIQUE INDEX contact_unique_idx ON contact (department_id, channel_type, value);

ALTER TABLE road_segment
    ADD COLUMN ref text, ADD COLUMN row_m real, ADD COLUMN pwd_road_id text,
    ADD COLUMN ward_no int;
-- 'within 25 m' uses ::geography; this index lets that search use it.
CREATE INDEX road_segment_geog_idx ON road_segment USING gist ((geom::geography));

CREATE TABLE report_event (
    id bigserial PRIMARY KEY,
    report_id uuid NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('user', 'assistant')),
    kind text NOT NULL,                  -- report | question | answer | emergency | dossier
                                         -- | rejected | out_of_coverage
    payload jsonb NOT NULL DEFAULT '{}', -- masked text only
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX report_event_report_idx ON report_event (report_id, id);
ALTER TABLE report_event ENABLE ROW LEVEL SECURITY;
"""

DOWN = """
DROP TABLE report_event;
DROP INDEX road_segment_geog_idx;
ALTER TABLE road_segment DROP COLUMN ref, DROP COLUMN row_m, DROP COLUMN pwd_road_id,
    DROP COLUMN ward_no;
DROP INDEX contact_unique_idx;
ALTER TABLE department DROP COLUMN level, DROP COLUMN owns, DROP COLUMN jurisdiction_type,
    DROP COLUMN helpline, DROP COLUMN grievance_url, DROP COLUMN email, DROP COLUMN source_url,
    DROP COLUMN accessed, DROP COLUMN verified;
DROP TABLE routing_rule CASCADE;
ALTER TABLE master_issue ALTER COLUMN routing_rule_id TYPE int USING NULL;
ALTER TABLE routing_decision ALTER COLUMN rule_id TYPE int USING NULL;
ALTER TABLE outcome_feedback ALTER COLUMN routing_rule_id TYPE int USING NULL;
CREATE TABLE routing_rule (
    id serial PRIMARY KEY, priority int NOT NULL, ward_ids int[], category text,
    row_class text, special_zone text, asset text,
    department_id int NOT NULL REFERENCES department(id), sla_days int,
    sla_citation_id int REFERENCES citation(id), source_citation_ids int[], confidence real,
    status text NOT NULL DEFAULT 'active', dataset_version int REFERENCES dataset_version(id),
    last_verified date
);
-- as 0001 left it: the lookup index, RLS and the 3 FKs to the int id
CREATE INDEX routing_rule_lookup_idx ON routing_rule (dataset_version, category, priority);
ALTER TABLE routing_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_issue ADD FOREIGN KEY (routing_rule_id) REFERENCES routing_rule(id);
ALTER TABLE routing_decision ADD FOREIGN KEY (rule_id) REFERENCES routing_rule(id);
ALTER TABLE outcome_feedback ADD FOREIGN KEY (routing_rule_id) REFERENCES routing_rule(id);
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
