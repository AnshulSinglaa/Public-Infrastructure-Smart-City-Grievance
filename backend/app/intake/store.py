"""DB writes for the end of intake: master issue, report update, dossier."""

from psycopg.types.json import Jsonb

from app.dossier.build import build_complaint
from app.privacy.text import decrypt, mask_pii

PIN_SQL = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"


def masked_text(conn, report_id: str) -> str | None:
    """The report's text, decrypted and masked: the only form that leaves the DB."""
    row = conn.execute("SELECT text FROM report WHERE id = %s", (report_id,)).fetchone()
    return mask_pii(decrypt(row[0])) if row and row[0] else None


def save_master(conn, r, category, media_id, lat, lng):
    """Insert the master issue (the problem itself, shared by later reporters); return its id."""
    dept_id = conn.execute(
        "SELECT id FROM department WHERE code = %s", (r["department_code"],)
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO master_issue (category, centroid, ward_id, representative_media_id,"
        " department_id, routing_confidence, sla_days, state)"
        f" VALUES (%s, {PIN_SQL}, %s, %s, %s, %s, %s, 'dossier_ready') RETURNING id",
        (category, lng, lat, r["ward_id"], media_id, dept_id, r["confidence"], r["sla_days"]),
    ).fetchone()[0]


def save_dossier(conn, master_id, report_id, r, category, severity, citizen_text, lat, lng,
                 emergency=False):
    """Insert the dossier (pin, routing, English letter, deadline); return its id.
    citizen_text is already masked: dossiers are made to be shared. An emergency has no
    15-day deadline: the letter asks for immediate action (B-037)."""
    complaint = build_complaint(category, severity, r, citizen_text, emergency)
    pin = {"lat": lat, "lng": lng, "ward_no": r["ward_no"], "ward_name": r["ward_name"]}
    routing = {"confidence_label": r["confidence_label"], "curated": r["curated"],
               "fake": r["fake"]}
    deadline = ({"sla_days": None, "label": "immediate"} if emergency
                else {"sla_days": r["sla_days"], "label": "suggested"})
    return conn.execute(
        "INSERT INTO dossier (master_id, report_id, pin, routing, complaint_en, citizen_text,"
        " deadline) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (master_id, report_id, Jsonb(pin), Jsonb(routing), complaint, citizen_text,
         Jsonb(deadline)),
    ).fetchone()[0]


def update_report(conn, report_id, state, **fields):
    """Set the report's state plus any of: master_id, category, severity, intake_confidence,
    intake, junk_reason, photo_quality."""
    if "intake" in fields:
        fields["intake"] = Jsonb(fields["intake"])
    sets = "".join(f", {k} = %s" for k in fields)  # keys are ours, never user input
    conn.execute(f"UPDATE report SET state = %s{sets} WHERE id = %s",
                 (state, *fields.values(), report_id))
