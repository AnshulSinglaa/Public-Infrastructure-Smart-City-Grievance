"""Report endpoints: file a report, answer its clarify question, read its dossier, fetch its photo, list my reports.
Contracts: docs/contracts/sprint2-api.md, sprint3-api.md, sprint4-api.md. Everything here is owner-only.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from langgraph.types import Command
from PIL import Image
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import limits, tracing
from app.auth import current_user
from app.config import settings
from app.db import connect
from app.intake.catalog import categories
from app.intake.emergency import call_first
from app.intake.graph import config, graph
from app.intake.store import update_report
from app.privacy import photo as photo_privacy
from app.privacy.text import encrypt
from app.routing.engine import general_channels, in_delhi, state_directory

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
    """Save the clean original (private). The blurred copy is made later in the intake
    graph, once the vision model's face/plate boxes are known; until then blur_status is
    NULL, which means "not shareable"."""
    media_id = uuid.uuid4()
    key = f"{media_id}.jpg"
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    # ponytail: if a later insert fails, this file is left orphaned; a cleanup job can
    # delete files with no media row once that matters.
    (settings.media_dir / key).write_bytes(photo_privacy.to_jpeg(img))
    conn.execute(
        "INSERT INTO media (id, owner_id, kind, mime, storage_key)"
        " VALUES (%s, %s, 'photo', 'image/jpeg', %s)",
        (media_id, user_id, key),
    )
    return media_id


def save_draft(conn, user_id, media_id, ui_lang, text, pin):
    """Insert the report as a draft (text encrypted) and return its id."""
    return conn.execute(
        "INSERT INTO report (user_id, media_id, state, ui_lang, text, pin, pin_source,"
        " capture_gps, location_verified)"
        f" VALUES (%s, %s, 'draft', %s, %s, {PIN_SQL}, %s, %s, %s) RETURNING id",
        (user_id, media_id, ui_lang, encrypt(text) if text else None, pin["lng"], pin["lat"],
         pin["pin_source"], Jsonb({"accuracy_m": pin["accuracy_m"]}), pin["location_verified"]),
    ).fetchone()[0]


def run_intake(report_id: str, graph_input, notices: list[str]):
    """Run (or resume) the intake graph, then turn where it stopped into the API envelope."""
    try:
        result = graph().invoke(graph_input, config(report_id))
    finally:
        tracing.flush()  # send this report's spans now (no-op when tracing is off)
    body = {"report_id": report_id, "next": None, "master_id": None, "dossier_id": None,
            "notices": notices}
    with connect() as conn:
        kind = result.get("emergency_kind")
        if kind:  # dangerous: show "call first" numbers, but never block the report
            body["emergency"] = call_first(conn, kind, result["lat"], result["lng"])
        # The model never looked at the photo (down, or skipped for privacy): say so (B-032).
        if result.get("image_down") or result.get("image_skipped"):
            body["notices"] = [*notices, "photo_not_checked"]
        if result.get("__interrupt__"):  # paused at clarify_category
            update_report(conn, report_id, "needs_clarification")
            return body | {"state": "needs_clarification",
                           "next": result["__interrupt__"][0].value}
        state = result["terminal"]
        if state == "ai_unavailable":  # kept, but not counted in the daily limit (B-033)
            update_report(conn, report_id, "ai_unavailable")
            return error(503, "ai_unavailable", "ai_unavailable")
        if state == "rate_limited_same_spot":
            return error(429, "rate_limited", "rate_limited_same_spot")
        if state == "rejected_junk":
            update_report(conn, report_id, "rejected_junk", junk_reason=result["junk_reason"])
            return body | {"state": state,
                           "next": {"kind": "retake_photo", "reason": result["junk_reason"]}}
        if state == "out_of_coverage":
            # state_directory is at the TOP level of the response, next to `next` (B-034).
            lat, lng = result["lat"], result["lng"]
            delhi = in_delhi(conn, lat, lng)
            # Outside Delhi our Delhi helplines can't help: no channels, the UI says so,
            # and that state's own portal (or CPGRAMS) is offered instead.
            body["next"] = {"kind": "general_channels", "outside_delhi": not delhi,
                            "channels": general_channels(conn) if delhi else []}
            if not delhi:
                body["state_directory"] = state_directory(conn, lat, lng)
            return body | {"state": state}
    return body | {"state": "dossier_ready", "master_id": result["master_id"],
                   "dossier_id": result["dossier_id"]}


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

    notices = ["fake_ai"] if settings.provider_mode == "fake" else []
    if accuracy_m is not None and accuracy_m > settings.gps_accuracy_max_m:
        notices.append("low_gps_accuracy")
    # location_verified is only the phone's claim ("taken with the in-app camera"); the server
    # can't check it. So it never changes routing, and the UI never calls a photo "verified".
    if img:
        notices.append("location_reported_by_phone" if location_verified
                       else "photo_unverified_location")
    pin = {"lat": lat, "lng": lng, "accuracy_m": accuracy_m, "pin_source": pin_source,
           "location_verified": location_verified}

    # 2. Rate limits (before any model call costs us), then save the draft and commit.
    with connect() as conn:
        # One report at a time per user: a parallel request waits here until this
        # transaction ends, so its rate-limit count sees this report (B-015).
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(user_id),))
        limited = limits.check(conn, user_id)
        if limited:
            return error(429, "rate_limited", limited)
        media_id = save_photo(conn, img, user_id) if img else None
        report_id = str(save_draft(conn, user_id, media_id, ui_lang, text, pin))

    # 3. The AI intake graph: models -> fuse -> (maybe pause to ask) -> SQL routing.
    return run_intake(report_id, {
        "report_id": report_id, "user_id": str(user_id), "lat": lat, "lng": lng,
        "media_id": str(media_id) if media_id else None, "has_text": bool(text)}, notices)


class ClarifyIn(BaseModel):
    category: str = Field(min_length=1, max_length=60)


@router.post("/reports/{report_id}/clarify")
def clarify_report(report_id: uuid.UUID, body: ClarifyIn, user_id: User):
    """Resume a paused report with the category the user picked."""
    with connect() as conn:
        owned = conn.execute("SELECT 1 FROM report WHERE id = %s AND user_id = %s",
                             (report_id, user_id)).fetchone()
    if not owned:
        return error(404, "not_found", "report_not_found")
    paused = graph().get_state(config(str(report_id))).interrupts
    question = paused[0].value if paused else None
    if not question or question.get("kind") != "clarify_category":
        return error(409, "wrong_step", "wrong_step")
    answer = body.category.strip()
    offered = {o["category"] for o in question["options"]}
    if answer not in offered and not (question["allow_other"] and answer in categories()):
        return error(422, "validation", "invalid_category")
    # Claim the paused report in one atomic UPDATE (TL#2): of two parallel answers only one
    # gets the row back; the other gets 409 instead of making a second dossier.
    with connect() as conn:
        claimed = conn.execute(
            "UPDATE report SET state = 'resuming' WHERE id = %s AND user_id = %s"
            " AND state = 'needs_clarification' RETURNING 1", (report_id, user_id)).fetchone()
    if not claimed:
        return error(409, "wrong_step", "wrong_step")
    return run_intake(str(report_id), Command(resume=answer), [])


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
    # "Withheld for privacy" only when blurring itself failed (B-032); a junk photo that
    # wasn't used is not "withheld".
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
        "photo_withheld": d["blur_status"] == "failed",
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


@router.get("/categories")
def list_categories(user_id: User):
    """The 37 codes from data/categories.csv, for the UI's "Something else" picker."""
    return {"categories": [{"code": code, "name_en": c["name_en"], "group": c["group"],
                            "emergency": c["emergency"] == "yes"}
                           for code, c in categories().items()]}


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
