"""The intake graph (ARCHITECTURE §3, simplified per §0):

  START -> assess_image (Gemini) --+
  START -> assess_text  (Groq)   --+-> fuse -> clarify (PAUSE for the user) -> route -> END
                                           \\-> route -> END        \\-> END (junk / AI down)

The models only describe the problem. The department is decided by `route`, which is
the same SQL as before: nothing a model says can change it.
The state is saved in Postgres after every step (thread_id = report_id), so a paused
report can be resumed by POST /reports/{id}/clarify, even after a restart.
"""

from functools import cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from PIL import Image

from app import limits
from app.config import settings
from app.db import connect
from app.intake import store
from app.intake.catalog import categories
from app.intake.emergency import emergency_kind
from app.privacy import photo as photo_privacy
from app.providers import gemini, groq
from app.providers.common import ProviderDown
from app.routing.engine import route as sql_route


class IntakeState(TypedDict, total=False):
    report_id: str
    user_id: str
    media_id: str | None
    has_text: bool
    lat: float
    lng: float
    image: dict | None        # ImageAssessment
    text_assess: dict | None  # TextAssessment
    image_down: bool
    image_skipped: bool       # not sent to the model: pre-blur failed (privacy)
    photo_junk: bool          # junk photo dropped because the text was usable
    text_down: bool
    category: str | None
    severity: str | None
    confidence: float
    emergency_kind: str | None
    clarify: dict | None      # the question we pause on
    terminal: str | None      # dossier_ready | out_of_coverage | rejected_junk | ai_unavailable
                              # | rate_limited_same_spot
    junk_reason: str | None
    master_id: str | None
    dossier_id: str | None


def media_file(media_id: str):
    with connect() as conn:
        key = conn.execute("SELECT storage_key FROM media WHERE id = %s", (media_id,)).fetchone()[0]
    return settings.media_dir / key


# ---------- nodes ----------

def assess_image(state: IntakeState) -> dict:
    if not state.get("media_id"):
        return {}
    # SEC-013: Gemini only ever sees a copy with the OpenCV faces/plates already blurred
    # (same size, so the boxes it returns still fit the original). If that pre-blur fails,
    # the photo is not sent at all (our choice, not an outage): the text decides, or we ask.
    # The shared copy is made later in blur_photo: OpenCV + model boxes on the original.
    pre_blurred = photo_privacy.blurred_jpeg(Image.open(media_file(state["media_id"])))
    if pre_blurred is None:
        return {"image_skipped": True}
    try:
        return {"image": gemini.assess_image(pre_blurred, state["report_id"])}
    except ProviderDown:
        return {"image_down": True}


def assess_text(state: IntakeState) -> dict:
    if not state.get("has_text"):
        return {}
    with connect() as conn:
        text = store.masked_text(conn, state["report_id"])  # masked before it leaves us
    try:
        return {"text_assess": groq.assess_text(text, state["report_id"])}
    except ProviderDown:
        return {"text_down": True}


def options(*assessments) -> list[dict]:
    """The top-2 distinct categories the models suggested, best first."""
    seen, out = set(), []
    for a in sorted((a for a in assessments if a and a["category"]),
                    key=lambda a: a["confidence"], reverse=True):
        if a["category"] not in seen:
            seen.add(a["category"])
            out.append({"category": a["category"], "confidence": a["confidence"]})
    return out[:2]


def fuse(state: IntakeState) -> dict:
    """Plain code, no model: decide junk / ask / go on."""
    img, txt = state.get("image"), state.get("text_assess")
    threshold = settings.ask_threshold
    if img is None and txt is None:
        if state.get("image_skipped") and not state.get("text_down"):
            # No model is down: we kept the photo from the model for privacy. Ask the user.
            return {"clarify": {"kind": "clarify_category", "options": [],
                                "allow_other": True, "mismatch": None}}
        return {"terminal": "ai_unavailable"}  # every model we needed was down (503)
    if img and img["junk"]:
        if not (txt and txt["category"]):
            return {"terminal": "rejected_junk", "junk_reason": img["junk_reason"] or "unrelated"}
        # The text is usable: drop the junk photo (never shared) and decide from the text.
        return fuse({**state, "image": None}) | {"photo_junk": True}
    out = {"image": img, "emergency_kind": emergency_kind(img, txt, None)}
    both_sure = img and txt and img["category"] and txt["category"]         and min(img["confidence"], txt["confidence"]) >= threshold
    if both_sure and img["category"] != txt["category"]:
        return out | {"clarify": {"kind": "clarify_category", "options": options(img, txt),
                                  "allow_other": True,
                                  "mismatch": {"image": img["category"], "text": txt["category"]}}}
    best = options(img, txt)
    if not best or best[0]["confidence"] < threshold:
        return out | {"clarify": {"kind": "clarify_category", "options": best,
                                  "allow_other": True, "mismatch": None}}
    return out | {"category": best[0]["category"], "confidence": best[0]["confidence"],
                  "clarify": None}


def after_fuse(state: IntakeState) -> str:
    if state.get("terminal"):
        return END
    return "clarify" if state.get("clarify") else "route"


def clarify(state: IntakeState) -> dict:
    """PAUSE. LangGraph saves the state and returns; the user's answer comes back as the
    value of interrupt() when /clarify resumes the graph. The answer counts as certain."""
    answer = interrupt(state["clarify"])
    return {"category": answer, "confidence": 1.0, "clarify": None}


def route(state: IntakeState) -> dict:
    """Same-spot limit, blur, the SQL routing, then the master issue + dossier."""
    rid, cat, lat, lng = state["report_id"], state["category"], state["lat"], state["lng"]
    img, txt = state.get("image"), state.get("text_assess")
    kind = emergency_kind(img, txt, cat)
    # Severity is never the model's free choice (B-035: "Severity critical!!" in the text
    # was obeyed). It is the category's default, raised to critical only by the emergency
    # flag (Sprint 5 adds the night-safety rule).
    severity = "critical" if kind else categories()[cat]["default_severity"]
    intake = {"image": img, "text": txt, "fake": settings.provider_mode == "fake"}
    with connect() as conn:
        # Same per-user lock as POST /reports, so two parallel reports can't both pass.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (state["user_id"],))
        if limits.same_spot_hit(conn, state["user_id"], cat, lat, lng, rid):
            store.update_report(conn, rid, "rate_limited")
            return {"terminal": "rate_limited_same_spot"}
        if state.get("media_id"):
            blur_photo(conn, state["media_id"], img, state.get("photo_junk", False))
        fields = {"category": cat, "severity": severity, "intake_confidence": state["confidence"],
                  "intake": intake, "photo_quality": "low" if img and img["is_dark"] else "ok"}
        r = sql_route(conn, cat, lat, lng)
        if r is None:  # out of coverage: report only, no department claim
            store.update_report(conn, rid, "out_of_coverage", **fields)
            return {"terminal": "out_of_coverage", "emergency_kind": kind, "severity": severity}
        media_id = state.get("media_id")
        master_id = store.save_master(conn, r, cat, media_id, lat, lng)
        store.update_report(conn, rid, "dossier_ready", master_id=master_id, **fields)
        dossier_id = store.save_dossier(conn, master_id, rid, r, cat, severity,
                                        store.masked_text(conn, rid), lat, lng, bool(kind))
    return {"terminal": "dossier_ready", "master_id": str(master_id),
            "dossier_id": str(dossier_id), "emergency_kind": kind, "severity": severity}


def blur_photo(conn, media_id: str, img: dict | None, junk: bool) -> None:
    """Blurred copy = OpenCV faces/plates, plus the vision model's boxes when it answered.
    A vision-model outage does NOT withhold the photo (B-032): OpenCV still blurs it.
    blur_status: 'ok' (shareable) | 'failed' (blurring itself broke: withheld for privacy)
    | 'junk' (not a civic photo: not used, and not called "withheld")."""
    if junk:
        conn.execute("UPDATE media SET blur_status = 'junk' WHERE id = %s", (media_id,))
        return
    picture = Image.open(media_file(media_id))
    model_boxes = (img["face_boxes"] + img["plate_boxes"]) if img else []
    blurred = photo_privacy.blurred_jpeg(picture,
                                         photo_privacy.from_model_boxes(picture, model_boxes))
    key = f"{media_id}_blur.jpg" if blurred else None
    if blurred:
        (settings.media_dir / key).write_bytes(blurred)
    conn.execute("UPDATE media SET blurred_key = %s, blur_status = %s WHERE id = %s",
                 (key, "ok" if blurred else "failed", media_id))


# ---------- wiring ----------

@cache
def graph():
    """Built once. The checkpointer keeps a small pool of its own DB connections."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(settings.database_url, min_size=1, max_size=4, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0,
                                  "row_factory": dict_row})
    g = StateGraph(IntakeState)
    for name, node in [("assess_image", assess_image), ("assess_text", assess_text),
                       ("fuse", fuse), ("clarify", clarify), ("route", route)]:
        g.add_node(name, node)
    g.add_edge(START, "assess_image")  # the two assess nodes run in parallel
    g.add_edge(START, "assess_text")
    g.add_edge(["assess_image", "assess_text"], "fuse")  # fuse waits for both
    g.add_conditional_edges("fuse", after_fuse, ["clarify", "route", END])
    g.add_edge("clarify", "route")
    g.add_edge("route", END)
    return g.compile(checkpointer=PostgresSaver(pool))


def config(report_id: str) -> dict:
    return {"configurable": {"thread_id": report_id}}
