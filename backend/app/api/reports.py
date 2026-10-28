"""Sprint 2 endpoints: file a report, read its dossier, fetch its photo.
Contract: docs/contracts/sprint2-api.md
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import settings
from app.db import connect
from app.dossier.build import build_complaint
from app.providers.fakes import fake_intake
from app.routing.engine import general_channels, route

router = APIRouter(prefix="/api/v1")

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 1000
# Check the file's first bytes, not the client's Content-Type (that can say anything).
MAGIC = {b"\xff\xd8\xff": ("image/jpeg", ".jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", ".png")}


def error(status: int, code: str, message_key: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message_key": message_key}}, status_code=status)


def sniff(data: bytes) -> tuple[str, str] | None:
    for magic, kind in MAGIC.items():
        if data.startswith(magic):
            return kind
    return None


PIN_SQL = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"  # NULL lng/lat -> NULL point


def save_photo(conn, data: bytes, kind: tuple[str, str]) -> uuid.UUID:
    """Write the photo to media_dir, add its media row, return the media id."""
    mime, ext = kind
    media_id = uuid.uuid4()
    key = f"{media_id}{ext}"
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    # ponytail: if a later insert fails, this file is left orphaned; a cleanup job can
    # delete files with no media row once that matters.
    (settings.media_dir / key).write_bytes(data)
    conn.execute(
        "INSERT INTO media (id, kind, mime, storage_key) VALUES (%s, 'photo', %s, %s)",
        (media_id, mime, key),
    )
    return media_id


def save_report(conn, master_id, state, media_id, ui_lang, intake, lat, lng):
    """Insert one report row and return its id."""
    # ponytail: report.text is a Fernet (bytea) column; it stays empty until the privacy
    # module adds encryption. The text still reaches the dossier complaint.
    return conn.execute(
        "INSERT INTO report (master_id, media_id, state, ui_lang, category, severity,"
        " intake_confidence, intake, pin, pin_source)"
        f" VALUES (%s, %s, %s, %s, %s, %s, %s, %s, {PIN_SQL}, %s) RETURNING id",
        (master_id, media_id, state, ui_lang, intake["category"], intake["severity"],
         intake["confidence"], Jsonb(intake), lng, lat, "gps" if lat is not None else None),
    ).fetchone()[0]


def save_master(conn, r, intake, media_id, lat, lng):
    """Insert the master issue (the problem itself, shared by later reporters); return its id."""
    dept_id = conn.execute(
        "SELECT id FROM department WHERE code = %s", (r["department_code"],)
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO master_issue (category, centroid, ward_id, representative_media_id,"
        " department_id, routing_confidence, sla_days, state)"
        f" VALUES (%s, {PIN_SQL}, %s, %s, %s, %s, %s, 'dossier_ready') RETURNING id",
        (intake["category"], lng, lat, r["ward_id"], media_id, dept_id,
         r["confidence"], r["sla_days"]),
    ).fetchone()[0]


def save_dossier(conn, master_id, report_id, r, intake, text, lat, lng):
    """Insert the dossier (pin, routing, hi/en complaint, deadline); return its id."""
    complaint = build_complaint(intake["category"], intake["severity"], r, text)
    pin = {"lat": lat, "lng": lng, "ward_no": r["ward_no"], "ward_name": r["ward_name"]}
    routing = {"confidence_label": r["confidence_label"], "curated": r["curated"],
               "fake": r["fake"]}
    deadline = {"sla_days": r["sla_days"], "label": "suggested"}
    return conn.execute(
        "INSERT INTO dossier (master_id, report_id, pin, routing, complaint_hi, complaint_en,"
        " deadline) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (master_id, report_id, Jsonb(pin), Jsonb(routing), complaint["hi"], complaint["en"],
         Jsonb(deadline)),
    ).fetchone()[0]


@router.post("/reports", status_code=201)
def create_report(
    # A list, so a second `photo` part is seen and rejected instead of silently dropped.
    photo: Annotated[list[UploadFile] | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Form(ge=-180, le=180)] = None,
    ui_lang: Annotated[Literal["hi", "en"], Form()] = "hi",
):
    # 1. Validate input.
    if photo and len(photo) > 1:
        return error(422, "validation", "too_many_photos")
    photo = photo[0] if photo else None
    text = (text or "").strip() or None
    data = photo.file.read(MAX_PHOTO_BYTES + 1) if photo else b""
    if not data and not text:
        return error(422, "validation", "need_photo_or_text")
    if text and len(text) > MAX_TEXT_CHARS:
        return error(422, "validation", "text_too_long")
    if (lat is None) != (lng is None):
        return error(422, "validation", "need_lat_and_lng")
    kind = None
    if data:
        if len(data) > MAX_PHOTO_BYTES:
            return error(422, "validation", "photo_too_large")
        kind = sniff(data)
        if not kind:
            return error(422, "validation", "photo_not_jpeg_png")

    # 2. Fake AI, then save everything in one transaction (commits at the end).
    intake = fake_intake(data or None, text)
    with connect() as conn:
        media_id = save_photo(conn, data, kind) if kind else None
        r = route(conn, intake["category"], lat, lng)
        if r is None:  # out of coverage: report only, no claim, just general helplines
            report_id = save_report(conn, None, "out_of_coverage", media_id, ui_lang, intake,
                                    lat, lng)
            return {
                "report_id": str(report_id),
                "state": "out_of_coverage",
                "next": {"kind": "general_channels", "channels": general_channels(conn)},
                "master_id": None,
                "dossier_id": None,
                "notices": ["fake_ai"],
            }
        master_id = save_master(conn, r, intake, media_id, lat, lng)
        report_id = save_report(conn, master_id, "dossier_ready", media_id, ui_lang, intake,
                                lat, lng)
        dossier_id = save_dossier(conn, master_id, report_id, r, intake, text, lat, lng)

    return {
        "report_id": str(report_id),
        "state": "dossier_ready",
        "next": None,
        "master_id": str(master_id),
        "dossier_id": str(dossier_id),
        "notices": ["fake_ai"],
    }


@router.get("/dossiers/{dossier_id}")
def get_dossier(dossier_id: uuid.UUID):
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        d = cur.execute(
            "SELECT d.*, r.category, r.severity, m.department_id, m.representative_media_id,"
            " dep.code, dep.name_en, dep.name_hi"
            " FROM dossier d JOIN report r ON r.id = d.report_id"
            " JOIN master_issue m ON m.id = d.master_id"
            " JOIN department dep ON dep.id = m.department_id"
            " WHERE d.id = %s",
            (dossier_id,),
        ).fetchone()
        if not d:
            return error(404, "not_found", "dossier_not_found")
        channels = cur.execute(
            "SELECT channel_type AS type, label, value, deep_link FROM contact"
            " WHERE department_id = %s ORDER BY id",
            (d["department_id"],),
        ).fetchall()
    media_id = d["representative_media_id"]
    return {
        "id": str(d["id"]),
        "category": d["category"],
        "severity": d["severity"],
        "pin": d["pin"],
        "department": {"code": d["code"], "name_en": d["name_en"], "name_hi": d["name_hi"]},
        "routing": d["routing"],
        "complaint": {"hi": d["complaint_hi"], "en": d["complaint_en"]},
        "claims": d["claims"],
        "deadline": d["deadline"],
        "reporter_count": d["reporter_count"],
        "channels": channels,
        "photo_url": f"/api/v1/media/{media_id}" if media_id else None,
    }


@router.get("/media/{media_id}")
def get_media(media_id: uuid.UUID):
    with connect() as conn:
        row = conn.execute(
            "SELECT storage_key, mime FROM media WHERE id = %s", (media_id,)
        ).fetchone()
    # storage_key is written only by us (uuid + ext), so it cannot point outside media_dir.
    if not row or not (settings.media_dir / row[0]).is_file():
        return error(404, "not_found", "media_not_found")
    return FileResponse(settings.media_dir / row[0], media_type=row[1])
