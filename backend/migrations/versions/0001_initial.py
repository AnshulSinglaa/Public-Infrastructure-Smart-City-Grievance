"""Initial schema: every table from ARCHITECTURE §6, simplified per §0.

All geometry is SRID 4326 (lon/lat); use ::geography when you need metres.
P2/P3 tables are created now so nothing is re-modelled later.
`bytea` columns marked "Fernet" hold ciphertext once the privacy module lands.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None

TABLES = [
    "app_user", "dataset_version", "policy_doc", "chunk", "citation", "department", "contact",
    "ward", "special_zone", "road_segment", "routing_rule", "media", "master_issue", "report",
    "master_reporter", "routing_decision", "dossier", "share_link", "escalation_step",
    "vote_round", "resolution_vote", "check_in", "notifications", "outcome_feedback",
]

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- people ----------
CREATE TABLE app_user (
    id uuid PRIMARY KEY,                 -- = Supabase auth uid (email lives in Supabase only)
    name bytea,                          -- Fernet
    phone bytea,                         -- Fernet, optional
    preferred_language text NOT NULL DEFAULT 'hi',
    reminder_email_opt_in boolean NOT NULL DEFAULT false,
    text_size int NOT NULL DEFAULT 100,
    privacy_accepted_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- curated knowledge (RAG) ----------
CREATE TABLE dataset_version (
    id serial PRIMARY KEY,
    frozen_at timestamptz NOT NULL DEFAULT now(),
    sha256 text NOT NULL
);

CREATE TABLE policy_doc (
    id serial PRIMARY KEY,
    title text NOT NULL,
    url text,
    lang text,
    meta jsonb NOT NULL DEFAULT '{}',    -- the rest of the manifest fields
    ocr boolean NOT NULL DEFAULT false,
    manually_checked boolean NOT NULL DEFAULT false
);

CREATE TABLE chunk (
    id serial PRIMARY KEY,
    doc_id int NOT NULL REFERENCES policy_doc(id) ON DELETE CASCADE,
    section_ref text,
    page int,
    text text NOT NULL,
    text_norm text,
    lang text,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text_norm, text))) STORED,
    embedding vector(1024)
);
CREATE INDEX chunk_tsv_idx ON chunk USING gin (tsv);
CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops);

CREATE TABLE citation (
    id serial PRIMARY KEY,
    chunk_id int NOT NULL REFERENCES chunk(id),
    quoted_span text NOT NULL
);

-- ---------- departments & contacts ----------
CREATE TABLE department (
    id serial PRIMARY KEY,
    code text NOT NULL UNIQUE,
    name_en text NOT NULL,
    name_hi text NOT NULL
);

CREATE TABLE contact (
    id serial PRIMARY KEY,
    department_id int NOT NULL REFERENCES department(id),
    channel_type text NOT NULL,          -- helpline / email / portal / app
    label text,
    value text NOT NULL,
    deep_link text,
    scope text,
    designation text,
    last_verified date,
    source_url text
);

-- ---------- geography ----------
CREATE TABLE ward (
    id serial PRIMARY KEY,
    ward_no int NOT NULL UNIQUE,
    name_en text NOT NULL,
    name_hi text,
    mcd_zone text,
    civic_body text NOT NULL DEFAULT 'MCD',
    geom geometry(MultiPolygon, 4326) NOT NULL,
    in_coverage boolean NOT NULL DEFAULT false,
    source text,
    version text
);
CREATE INDEX ward_geom_idx ON ward USING gist (geom);

CREATE TABLE special_zone (
    id serial PRIMARY KEY,
    type text NOT NULL,
    name text,
    geom geometry(MultiPolygon, 4326) NOT NULL,
    priority int NOT NULL DEFAULT 0,
    source_citation_id int REFERENCES citation(id),
    last_verified date
);
CREATE INDEX special_zone_geom_idx ON special_zone USING gist (geom);

CREATE TABLE road_segment (
    id serial PRIMARY KEY,
    osm_way_id bigint,
    name text,
    highway_class text,
    geom geometry(LineString, 4326) NOT NULL,
    pwd_listed boolean NOT NULL DEFAULT false,
    pwd_list_citation_id int REFERENCES citation(id),
    row_class text,
    row_source text,
    confidence real,
    last_verified date
);
CREATE INDEX road_segment_geom_idx ON road_segment USING gist (geom);
CREATE INDEX road_segment_osm_idx ON road_segment (osm_way_id);

CREATE TABLE routing_rule (
    id serial PRIMARY KEY,
    priority int NOT NULL,
    ward_ids int[],
    category text,
    row_class text,
    special_zone text,
    asset text,
    department_id int NOT NULL REFERENCES department(id),
    sla_days int,
    sla_citation_id int REFERENCES citation(id),
    source_citation_ids int[],
    confidence real,
    status text NOT NULL DEFAULT 'active',
    dataset_version int REFERENCES dataset_version(id),
    last_verified date
);
CREATE INDEX routing_rule_lookup_idx ON routing_rule (dataset_version, category, priority);

-- ---------- reports & issues ----------
CREATE TABLE media (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid REFERENCES app_user(id),
    kind text NOT NULL DEFAULT 'photo',  -- photo / after_photo (no voice: §0)
    mime text NOT NULL,
    storage_key text NOT NULL,           -- file name under MEDIA_DIR
    blurred_key text,
    blur_status text,                    -- ok / failed
    clip vector(512),
    quality text,
    purge_after timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX media_clip_idx ON media USING hnsw (clip vector_cosine_ops);

CREATE TABLE master_issue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    category text NOT NULL,
    centroid geometry(Point, 4326),      -- = first pin
    ward_id int REFERENCES ward(id),
    representative_media_id uuid REFERENCES media(id),
    department_id int REFERENCES department(id),
    routing_rule_id int REFERENCES routing_rule(id),
    routing_confidence real,
    alternatives jsonb,
    routing_epoch int NOT NULL DEFAULT 1,
    clock_started_at timestamptz,
    sla_days int,
    deadline_cited boolean NOT NULL DEFAULT false,
    sla_citation_id int REFERENCES citation(id),
    state text NOT NULL,
    prior_state text,
    last_seen_at timestamptz,
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    previous_master_id uuid REFERENCES master_issue(id),
    resolved_at timestamptz,
    is_sample boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX master_issue_centroid_idx ON master_issue USING gist (centroid);
CREATE INDEX master_issue_cat_state_idx ON master_issue (category, state);

CREATE TABLE report (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid REFERENCES app_user(id),
    master_id uuid REFERENCES master_issue(id),
    media_id uuid REFERENCES media(id),
    state text NOT NULL DEFAULT 'draft',
    ui_lang text NOT NULL DEFAULT 'hi',
    text bytea,                          -- Fernet
    category text,
    severity text,
    intake_confidence real,
    intake jsonb,                        -- raw model output
    junk_reason text,
    photo_quality text,
    location_verified boolean NOT NULL DEFAULT false,
    capture_gps jsonb,
    pin geometry(Point, 4326),
    pin_source text,
    drag_m real,
    landmark_text bytea,                 -- Fernet
    filed_channel text,
    ref_no text,
    filed_at timestamptz,
    filed_by text,
    routing_epoch int NOT NULL DEFAULT 1,
    opt_in_identity boolean NOT NULL DEFAULT false,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX report_pin_idx ON report USING gist (pin);
CREATE INDEX report_user_created_idx ON report (user_id, created_at);

CREATE TABLE master_reporter (
    master_id uuid REFERENCES master_issue(id),
    user_id uuid REFERENCES app_user(id),
    report_id uuid REFERENCES report(id),
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (master_id, user_id)
);

CREATE TABLE routing_decision (
    id serial PRIMARY KEY,
    report_id uuid NOT NULL REFERENCES report(id),
    master_id uuid REFERENCES master_issue(id),
    epoch int NOT NULL DEFAULT 1,
    ward_id int REFERENCES ward(id),
    special_zone_id int REFERENCES special_zone(id),
    road_segment_id int REFERENCES road_segment(id),
    snap_m real,
    image_judgement jsonb,
    user_answer text,
    evidence text[],
    rule_id int REFERENCES routing_rule(id),
    dataset_version int REFERENCES dataset_version(id),
    confidence real,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dossier (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    master_id uuid NOT NULL REFERENCES master_issue(id),
    report_id uuid NOT NULL REFERENCES report(id),
    version int NOT NULL DEFAULT 1,
    epoch int NOT NULL DEFAULT 1,
    pin jsonb,                           -- snapshot: lat, lng, ward_no, ward_name
    routing jsonb,                       -- snapshot: confidence_label, curated, ...
    complaint_hi text NOT NULL,
    complaint_en text NOT NULL,
    claims jsonb NOT NULL DEFAULT '[]',
    email_draft jsonb,
    deep_link text,
    call_script_hi text,
    call_script_en text,
    deadline jsonb,
    reporter_count int NOT NULL DEFAULT 1,
    prompt_version text,
    generation_ms int,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE share_link (
    id serial PRIMARY KEY,
    report_id uuid NOT NULL REFERENCES report(id),
    token text NOT NULL UNIQUE,          -- random token, no revoke (§0)
    helper_ref_no text,
    helper_filed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- P2: tracker, votes, nudges ----------
CREATE TABLE escalation_step (
    id serial PRIMARY KEY,
    report_id uuid NOT NULL REFERENCES report(id),
    epoch int NOT NULL DEFAULT 1,
    level text NOT NULL,                 -- L0..L3, evidence_letter
    draft jsonb,
    citation_ids int[],
    unlocked_at timestamptz,
    due_at timestamptz,
    filed_at timestamptz,
    ref_no text
);

CREATE TABLE vote_round (
    id serial PRIMARY KEY,
    master_id uuid NOT NULL REFERENCES master_issue(id),
    after_media_id uuid REFERENCES media(id),
    submitter_id uuid REFERENCES app_user(id),
    r_frozen int NOT NULL,
    ai_score text,
    distance_m real,
    accuracy_m real,
    opened_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    outcome text
);

CREATE TABLE resolution_vote (
    round_id int REFERENCES vote_round(id),
    user_id uuid REFERENCES app_user(id),
    vote text NOT NULL,
    PRIMARY KEY (round_id, user_id)
);

CREATE TABLE check_in (
    id serial PRIMARY KEY,
    master_id uuid NOT NULL REFERENCES master_issue(id),
    user_id uuid REFERENCES app_user(id),
    kind text NOT NULL,
    checkpoint text,
    answer text,
    distance_m real,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- One table for cards + emails; the unique key makes the daily tick idempotent.
CREATE TABLE notifications (
    id serial PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_user(id),
    master_id uuid REFERENCES master_issue(id),
    report_id uuid REFERENCES report(id),
    checkpoint text NOT NULL,
    epoch int NOT NULL DEFAULT 1,
    channel text NOT NULL DEFAULT 'card', -- card / email
    sent_at timestamptz NOT NULL DEFAULT now(),
    seen_at timestamptz,
    UNIQUE (master_id, user_id, checkpoint, epoch)
);

-- P3
CREATE TABLE outcome_feedback (
    id serial PRIMARY KEY,
    report_id uuid NOT NULL REFERENCES report(id),
    routing_rule_id int REFERENCES routing_rule(id),
    outcome text NOT NULL,
    bounced_to_dept_id int REFERENCES department(id)
);

-- ---------- minimal seed: the one department + helpline Sprint 2 routes to ----------
INSERT INTO department (code, name_en, name_hi)
VALUES ('MCD', 'Municipal Corporation of Delhi', 'दिल्ली नगर निगम');
INSERT INTO contact (department_id, channel_type, label, value, deep_link, scope)
SELECT id, 'helpline', 'MCD 311', '155305', 'tel:155305', 'all' FROM department WHERE code = 'MCD';
"""


def upgrade() -> None:
    # exec_driver_sql sends the SQL as-is (op.execute would read ':155305' as a bind param).
    op.get_bind().exec_driver_sql(SCHEMA)
    # RLS on with no policies: only the API (table owner) talks to the DB, never the browser.
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
