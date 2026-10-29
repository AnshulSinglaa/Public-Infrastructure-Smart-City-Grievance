"""Report endpoints: file a report, read its dossier, fetch its photo, list my reports.
Contracts: docs/contracts/sprint2-api.md + sprint3-api.md. Everything here is owner-only.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import limits
from app.auth import current_user
from app.config import settings
from app.db import connect
from app.dossier.build import build_complaint
from app.privacy import photo as photo_privacy
from app.privacy.text import encrypt, mask_pii
from app.providers.fakes import fake_intake
from app.routing.engine import general_channels, in_delhi, route

router = APIRouter(prefix="/api/v1")
User = Annotated[uuid.UUID, Depends(current_user)]

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 1000
# Check the file's first bytes, not the client's Content-Type (that can say anything).
MAGIC = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


def error(status: int, code: str, message_key: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message_key": message_key}}, status_code=status)


def is_jpeg_or_png(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in MAGIC)


def has_visible_text(text: str) -> bool:
    """False for text made only of spaces or invisible characters like U+200B (zero-width
    space): isprintable() is False for those. The text itself is kept as written."""
    return any(c.isprintable() and not c.isspace() for c in text)


PIN_SQL = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"


def save_photo(conn, img: Image.Image, user_id) -> uuid.UUID:
    """Save the clean original (private) and, if blurring works, the blurred copy (shared)."""
    media_id = uuid.uuid4()
    original, blurred = photo_privacy.to_jpeg(img), photo_privacy.blurred_jpeg(img)
    key, blurred_key = f"{media_id}.jpg", (f"{media_id}_blur.jpg" if blurred else None)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    # ponytail: if a later insert fails, these files are left orphaned; a cleanup job can
    # delete files with no media row once that matters.
    (settings.media_dir / key).write_bytes(original)
    if blurred:
        (settings.media_dir / blurred_key).write_bytes(blurred)
    conn.execute(
        "INSERT INTO media (id, owner_id, kind, mime, storage_key, blurred_key, blur_status)"
        " VALUES (%s, %s, 'photo', 'image/jpeg', %s, %s, %s)",
        (media_id, user_id, key, blurred_key, "ok" if blurred else "failed"),
    )
    return media_id


def save_report(conn, user_id, master_id, state, media_id, ui_lang, intake, text, pin):
    """Insert one report row (text encrypted) and return its id."""
    return conn.execute(
        "INSERT INTO report (user_id, master_id, media_id, state, ui_lang, text, category,"
        " severity, intake_confidence, intake, pin, pin_source, capture_gps, location_verified)"
        f" VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {PIN_SQL}, %s, %s, %s) RETURNING id",
        (user_id, master_id, media_id, state, ui_lang, encrypt(text) if text else None,
         intake["category"], intake["severity"], intake["confidence"], Jsonb(intake),
         pin["lng"], pin["lat"], pin["pin_source"], Jsonb({"accuracy_m": pin["accuracy_m"]}),
         pin["location_verified"]),
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
    """Insert the dossier (pin, routing, English letter, deadline); return its id.
    Only MASKED text goes in: dossiers are made to be shared."""
    citizen_text = mask_pii(text) if text else None
    complaint = build_complaint(intake["category"], intake["severity"], r, citizen_text)
    pin = {"lat": lat, "lng": lng, "ward_no": r["ward_no"], "ward_name": r["ward_name"]}
    routing = {"confidence_label": r["confidence_label"], "curated": r["curated"],
               "fake": r["fake"]}
    deadline = {"sla_days": r["sla_days"], "label": "suggested"}
    return conn.execute(
        "INSERT INTO dossier (master_id, report_id, pin, routing, complaint_en, citizen_text,"
        " deadline) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (master_id, report_id, Jsonb(pin), Jsonb(routing), complaint, citizen_text,
         Jsonb(deadline)),
    ).fetchone()[0]


@router.post("/reports", status_code=201)
def create_report(
    user_id: User,
    # A list, so a second `photo` part is seen and rejected instead of silently dropped.
    photo: Annotated[list[UploadFile] | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    lat: Annotated[float | None, Form(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Form(ge=-180, le=180)] = None,
    accuracy_m: Annotated[float | None, Form(ge=0, le=100_000)] = None,  # le: rejects "inf"
    pin_source: Annotated[Literal["gps", "gps_moved", "landmark", "map_click"] | None,
                          Form()] = None,
    location_verified: Annotated[bool, Form()] = False,
    ui_lang: Annotated[Literal["hi", "en"], Form()] = "hi",
):
    # 1. Validate input.
    if photo and len(photo) > 1:
        return error(422, "validation", "too_many_photos")
    photo = photo[0] if photo else None
    text = text.strip() if text and has_visible_text(text) else None
    data = photo.file.read(MAX_PHOTO_BYTES + 1) if photo else b""
    if not data and not text:
        return error(422, "validation", "need_photo_or_text")
    if text and len(text) > MAX_TEXT_CHARS:
        return error(422, "validation", "text_too_long")
    if lat is None or lng is None:  # S3: the pin is confirmed on the map before submit
        return error(422, "validation", "need_pin")
    img = None
    if data:
        if len(data) > MAX_PHOTO_BYTES:
            return error(422, "validation", "photo_too_large")
        try:
            img = photo_privacy.clean(data) if is_jpeg_or_png(data) else None
        except (OSError, ValueError, Image.DecompressionBombError):
            img = None  # right magic bytes but not a readable image
        if img is None:
            return error(422, "validation", "photo_not_jpeg_png")

    notices = ["fake_ai"]
    if accuracy_m is not None and accuracy_m > settings.gps_accuracy_max_m:
        notices.append("low_gps_accuracy")
    # location_verified is only the phone's claim ("taken with the in-app camera"); the server
    # can't check it. So it never changes routing, and the UI never calls a photo "verified".
    if img:
        notices.append("location_reported_by_phone" if location_verified
                       else "photo_unverified_location")
    pin = {"lat": lat, "lng": lng, "accuracy_m": accuracy_m, "pin_source": pin_source,
           "location_verified": location_verified}

    # 2. Fake AI, rate limits, then save everything in one transaction (commits at the end).
    intake = fake_intake(data or None, text)
    with connect() as conn:
        # One report at a time per user: a parallel request waits here until this
        # transaction ends, so its rate-limit count sees this report (B-015).
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(user_id),))
        limited = limits.check(conn, user_id, intake["category"], lat, lng)
        if limited:
            return error(429, "rate_limited", limited)
        media_id = save_photo(conn, img, user_id) if img else None
        r = route(conn, intake["category"], lat, lng)
        if r is None:  # out of coverage: report only, no claim, just general helplines
            report_id = save_report(conn, user_id, None, "out_of_coverage", media_id, ui_lang,
                                    intake, text, pin)
            delhi = in_delhi(conn, lat, lng)
            return {
                "report_id": str(report_id),
                "state": "out_of_coverage",
                # Outside Delhi our Delhi helplines can't help: no channels, the UI says so.
                "next": {"kind": "general_channels", "outside_delhi": not delhi,
                         "channels": general_channels(conn) if delhi else []},
                "master_id": None,
                "dossier_id": None,
                "notices": notices,
            }
        master_id = save_master(conn, r, intake, media_id, lat, lng)
        report_id = save_report(conn, user_id, master_id, "dossier_ready", media_id, ui_lang,
                                intake, text, pin)
        dossier_id = save_dossier(conn, master_id, report_id, r, intake, text, lat, lng)

    return {
        "report_id": str(report_id),
        "state": "dossier_ready",
        "next": None,
        "master_id": str(master_id),
        "dossier_id": str(dossier_id),
        "notices": notices,
    }


@router.get("/dossiers/{dossier_id}")
def get_dossier(dossier_id: uuid.UUID, user_id: User):
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        # `r.user_id = %s`: someone else's dossier looks exactly like a missing one (404).
        d = cur.execute(
            "SELECT d.*, r.category, r.severity, r.media_id, md.blur_status, m.department_id,"
            " dep.code, dep.name_en, dep.name_hi"
            " FROM dossier d JOIN report r ON r.id = d.report_id"
            " JOIN master_issue m ON m.id = d.master_id"
            " JOIN department dep ON dep.id = m.department_id"
            " LEFT JOIN media md ON md.id = r.media_id"
            " WHERE d.id = %s AND r.user_id = %s",
            (dossier_id, user_id),
        ).fetchone()
        if not d:
            return error(404, "not_found", "dossier_not_found")
        channels = cur.execute(
            "SELECT channel_type AS type, label, value, deep_link FROM contact"
            " WHERE department_id = %s ORDER BY id",
            (d["department_id"],),
        ).fetchall()
    shareable = d["media_id"] and d["blur_status"] == "ok"
    return {
        "id": str(d["id"]),
        "category": d["category"],
        "severity": d["severity"],
        "pin": d["pin"],
        "department": {"code": d["code"], "name_en": d["name_en"], "name_hi": d["name_hi"]},
        "routing": d["routing"],
        "complaint": {"en": d["complaint_en"]},
        "citizen_text": d["citizen_text"],
        "claims": d["claims"],
        "deadline": d["deadline"],
        "reporter_count": d["reporter_count"],
        "channels": channels,
        # The blurred copy only; blur failed -> no photo at all ("Photo withheld for privacy").
        "photo_url": f"/api/v1/media/{d['media_id']}" if shareable else None,
        "photo_withheld": bool(d["media_id"]) and not shareable,
    }


@router.get("/media/{media_id}")
def get_media(media_id: uuid.UUID, user_id: User, original: bool = False):
    """The blurred copy by default; ?original=1 gives the owner their own clean original."""
    with connect() as conn:
        row = conn.execute(
            "SELECT storage_key, blurred_key FROM media WHERE id = %s AND owner_id = %s",
            (media_id, user_id),
        ).fetchone()
    key = row and (row[0] if original else row[1])
    # Keys are written only by us (uuid + suffix), so they cannot point outside media_dir.
    if not key or not (settings.media_dir / key).is_file():
        return error(404, "not_found", "media_not_found")
    return FileResponse(settings.media_dir / key, media_type="image/jpeg")


@router.get("/me/reports")
def my_reports(user_id: User):
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        # ponytail: no paging; at <= 10 reports a day the list stays short for a long time.
        rows = cur.execute(
            "SELECT r.id AS report_id, r.state, r.category, w.ward_no,"
            " dep.code AS department_code, r.created_at, d.id AS dossier_id"
            " FROM report r LEFT JOIN master_issue m ON m.id = r.master_id"
            " LEFT JOIN ward w ON w.id = m.ward_id"
            " LEFT JOIN department dep ON dep.id = m.department_id"
            " LEFT JOIN dossier d ON d.report_id = r.id"
            " WHERE r.user_id = %s ORDER BY r.created_at DESC",
            (user_id,),
        ).fetchall()
    for row in rows:
        row["report_id"] = str(row["report_id"])
        row["dossier_id"] = str(row["dossier_id"]) if row["dossier_id"] else None
        row["created_at"] = row["created_at"].isoformat()
    return {"reports": rows}
