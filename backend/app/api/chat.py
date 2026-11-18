"""Q&A endpoints (contract sprint6-api.md §4, §4A): POST /reports/{id}/ask, POST /chat,
GET /chat, DELETE /chat, and the 👍/👎 feedback for both. Login required; /ask is owner-only.

Before any model or embedding call, in one transaction under the per-user lock:
the same client_msg_id again -> the stored answer; the budget (limits.question_limit);
then the masked question and a hidden model_call row are written. If no answer gets stored
(error, busy, or the client went away mid-stream), both are undone (B-058).
The turn itself (guards, intent, retrieval, gateway, output checks) is app/rag/answer.py.
With `Accept: text/event-stream` the answer streams as server-sent events; otherwise the
final object comes back as JSON.
Home-chat rows are Fernet-encrypted like report text (REVIEW R-09): only the client_msg_id,
status and trace seed stay readable, for lookups.
"""

import csv
import json
import logging
import re
import threading
import uuid
from functools import cache
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app import limits, tracing
from app.api.reports import error
from app.auth import current_user
from app.config import ROOT
from app.db import connect
from app.guards.input import clean_question
from app.intake.catalog import categories, states
from app.intake.events import messages
from app.privacy.text import WORDCHAR, decrypt, encrypt, mask_pii
from app.rag import slots as slot_rules
from app.rag.answer import answer_text, report_facts, turn
from app.routing.engine import find_state, in_delhi

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")
User = Annotated[uuid.UUID, Depends(current_user)]
FIXED_WEIGHT = 0.25  # a 0-token reply counts as 1/4 of a question (D11)
HIDDEN_KEYS = ("text", "question", "client_msg_id", "trace_seed", "dropped", "slots")
PLAIN_KEYS = ("client_msg_id", "status", "trace_seed")  # stay readable in chat_event


class AskIn(BaseModel):
    question: str | None = None
    client_msg_id: str | None = None
    force_intent: Literal["general_civic"] | None = None


class ChatIn(BaseModel):
    message: str | None = None
    client_msg_id: str | None = None
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    state_code: str | None = Field(None, max_length=4)
    force_intent: Literal["general_civic"] | None = None


class FeedbackIn(BaseModel):
    vote: Literal["up", "down"]
    reason: Literal["wrong", "not_helpful", "other"] | None = None


def parse(text: str | None, client_msg_id: str | None) -> tuple[str, str] | None:
    """(masked question, client_msg_id) or None for 422 invalid_question."""
    question = clean_question(text)
    try:
        msg_id = str(uuid.UUID(client_msg_id or ""))
    except ValueError:
        return None
    return (mask_pii(question), msg_id) if question else None


def opened(payload: dict) -> dict:
    """A stored payload with its encrypted part (if any) decrypted."""
    if "enc" not in payload:
        return payload
    rest = {k: v for k, v in payload.items() if k != "enc"}
    return rest | json.loads(decrypt(payload["enc"].encode()))


class Thread:
    """Where a Q&A thread is stored: report_event for one report, chat_event for one user.
    Table and column names are ours, never user input."""

    def __init__(self, endpoint: str, owner):
        self.endpoint, self.owner = endpoint, str(owner)
        self.table, self.col = (("report_event", "report_id") if endpoint == "ask"
                                else ("chat_event", "user_id"))
        self.user_kind, self.answer_kind = (("ask", "ask_answer") if endpoint == "ask"
                                            else ("chat", "chat_answer"))

    def insert(self, conn, role: str, kind: str, payload: dict) -> int:
        if self.table == "chat_event" and kind in (self.user_kind, self.answer_kind):
            secret = json.dumps({k: v for k, v in payload.items() if k not in PLAIN_KEYS},
                                default=str)
            payload = {k: payload[k] for k in PLAIN_KEYS if k in payload} | {
                "enc": encrypt(secret).decode()}
        return conn.execute(f"INSERT INTO {self.table} ({self.col}, role, kind, payload)"
                            " VALUES (%s, %s, %s, %s) RETURNING id",
                            (self.owner, role, kind, Jsonb(payload, dumps=lambda o: json.dumps(
                                o, default=str)))).fetchone()[0]

    def find(self, conn, kind: str, msg_id: str):
        return conn.execute(f"SELECT id, payload FROM {self.table} WHERE {self.col} = %s"
                            " AND kind = %s AND payload->>'client_msg_id' = %s",
                            (self.owner, kind, msg_id)).fetchone()

    def history(self, conn, turns: int) -> list[dict]:
        """The last answers, oldest first. Blocked turns are never kept (REVIEW R-02)."""
        rows = conn.execute(f"SELECT payload FROM {self.table} WHERE {self.col} = %s"
                            " AND kind = %s AND payload->>'status' <> 'blocked'"
                            " ORDER BY id DESC LIMIT %s",
                            (self.owner, self.answer_kind, turns)).fetchall()
        return [opened(r[0]) for r in reversed(rows)]

    def update(self, conn, call_id: int, fields: dict) -> None:
        conn.execute(f"UPDATE {self.table} SET payload = payload || %s WHERE id = %s",
                     (Jsonb(fields), call_id))


def begin(thread: Thread, user_id, report_id, question: str, msg_id: str):
    """Under the per-user lock: (stored answer | error response | None, model_call id)."""
    try:
        with connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(user_id),))
            done = thread.find(conn, thread.answer_kind, msg_id)
            if done:  # a double click / rerun: the stored answer, no new calls, not counted
                return replay(stored(done)), None
            if thread.find(conn, thread.user_kind, msg_id):
                return error(409, "wrong_step", "qa_in_progress"), None
            limited = limits.question_limit(conn, user_id, report_id)
            if limited:
                return error(429, "rate_limited", limited), None
            thread.insert(conn, "user", thread.user_kind,
                          {"text": question, "client_msg_id": msg_id})
            call_id = thread.insert(conn, "user", "model_call",
                                    {"weight": 1, "client_msg_id": msg_id})
    except psycopg.errors.ForeignKeyViolation:  # a valid token, no app_user row (REVIEW B-06)
        return error(401, "unauthorized", "login_required"), None
    return None, call_id


def stored(row) -> dict:
    event_id, payload = row
    return {k: v for k, v in opened(payload).items() if k not in HIDDEN_KEYS} | {
        "message_id": event_id}


def replay(body: dict):
    """A stored answer as the same events a fresh one gives (sentences, then done)."""
    by_id, sent = {s["id"]: s for s in body.get("sources", [])}, set()
    for s in body.get("sentences", []):
        new = [by_id[c] for c in s["cites"] if c not in sent and c in by_id]
        sent |= set(s["cites"])
        yield "sentence", s | {"sources": new}
    yield "done", body


def run(thread: Thread, ctx: dict, call_id: int, msg_id: str):
    """The turn's events; "done" is stored first so its body carries the message_id.
    Unless an answer was stored, the question is undone at the end, also when the client
    closes the stream mid-way (GeneratorExit runs the `finally`, B-058)."""
    saved = False
    try:
        for event, data in turn(ctx):
            if event == "done":
                with connect() as conn:
                    fixed = data["status"] not in ("answered", "not_found", "out_of_scope")
                    thread.update(conn, call_id, {"weight": FIXED_WEIGHT if fixed else 1,
                                                  "tokens": data.get("tokens", 0)})
                    payload = data | {"text": answer_text(data), "question": ctx["question"],
                                      "client_msg_id": msg_id, "trace_seed": ctx["trace_seed"],
                                      "slots": ctx.get("slots")}
                    data = {k: v for k, v in data.items() if k != "dropped"}
                    data["message_id"] = thread.insert(conn, "assistant", thread.answer_kind,
                                                       payload)
                    saved = True
            yield event, data
    except Exception:
        log.exception("Q&A turn failed")
        yield "error", {"status": 500, "message_key": "server_error"}
    finally:
        if not saved:
            undo(thread, call_id, msg_id)
        # A slow Langfuse must not hold this worker (REVIEW B-10).
        threading.Thread(target=tracing.flush, daemon=True).start()


def undo(thread: Thread, call_id: int, msg_id: str) -> None:
    """Nothing was answered: the question doesn't count, and the same client_msg_id may retry."""
    with connect() as conn:
        thread.update(conn, call_id, {"weight": 0})
        conn.execute(f"DELETE FROM {thread.table} WHERE {thread.col} = %s AND kind = %s"
                     " AND payload->>'client_msg_id' = %s",
                     (thread.owner, thread.user_kind, msg_id))


def error_response(data: dict):
    if data["status"] == 429:  # busy: the free tier's per-minute limit (B-056)
        r = error(429, "rate_limited", data["message_key"])
        body = json.loads(r.body) | {"retry_after": data["retry_after"]}
        return Response(json.dumps(body), status_code=429, media_type="application/json",
                        headers={"Retry-After": str(data["retry_after"])})
    return error(data["status"], "provider_down" if data["status"] == 503 else "server_error",
                 data["message_key"])


def respond(request: Request, events):
    if "text/event-stream" in request.headers.get("accept", ""):
        lines = (f"event: {e}\ndata: {json.dumps(d, default=str)}\n\n" for e, d in events)
        return StreamingResponse(lines, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})
    try:
        for event, data in events:
            if event == "done":
                return data
            if event == "error":
                return error_response(data)
        return error(500, "server_error", "server_error")
    finally:
        events.close()  # runs run()'s `finally` now, not at garbage collection


# ---------- POST /reports/{id}/ask ----------

@router.post("/reports/{report_id}/ask")
def ask(report_id: uuid.UUID, body: AskIn, request: Request, user_id: User):
    with connect() as conn:
        row = conn.execute("SELECT state, ST_Y(pin), ST_X(pin) FROM report"
                           " WHERE id = %s AND user_id = %s", (report_id, user_id)).fetchone()
    if not row:
        return error(404, "not_found", "report_not_found")
    state, lat, lng = row
    if state not in ("dossier_ready", "out_of_coverage"):
        return error(409, "wrong_step", "qa_needs_dossier")
    parsed = parse(body.question, body.client_msg_id)
    if not parsed:
        return error(422, "validation", "invalid_question")
    question, msg_id = parsed
    thread = Thread("ask", report_id)
    early, call_id = begin(thread, user_id, report_id, question, msg_id)
    if early is not None:  # an error response, or the stored answer's events
        return early if isinstance(early, Response) else respond(request, early)
    with connect() as conn:
        delhi = in_delhi(conn, lat, lng)
        found = None if delhi else find_state(conn, lat, lng)
        facts, department, allowed, category = report_facts(conn, report_id)
        history = thread.history(conn, 2)
        code = "DL" if delhi else (found[0] if found else None)
        slots = slot_rules.extract(conn, question, code, history[-1].get("slots")
                                   if history else None)
    cat_name = categories().get(category or "", {}).get("name_en", "")
    slots["issue_type"] = slots["issue_type"] or slot_rules.issue_type(cat_name)
    ctx = {"endpoint": "ask", "question": question, "force_intent": body.force_intent,
           "trace_seed": f"ask-{msg_id}", "delhi": delhi, "state_code": code,
           "state_name": "Delhi" if delhi else (found[1] if found else None), "slots": slots,
           "facts": facts, "department": department, "allowed": allowed,
           "history": answered(history)}
    return respond(request, run(thread, ctx, call_id, msg_id))


def answered(history: list[dict]) -> list[tuple[str, str]]:
    """Memory for the prompt: answered turns only (masked question + verified text)."""
    return [(h.get("question", ""), h.get("text", "")) for h in history
            if h.get("status") == "answered"]


# ---------- POST /chat, GET /chat, DELETE /chat ----------

@cache
def place_names() -> list[tuple[str, str]]:
    """(lower-case name, state code): state names, then data/city_state.csv cities."""
    names = [(s["state_name"].casefold(), code) for code, s in states().items()]
    with (ROOT / "data" / "city_state.csv").open(encoding="utf-8", newline="") as f:
        names += [(r["city"].casefold(), r["state_code"]) for r in csv.DictReader(f)]
    return sorted(names, key=lambda n: len(n[0]), reverse=True)  # "new delhi" before "delhi"


def place_in(text: str) -> str | None:
    low = text.casefold()
    for name, code in place_names():
        if re.search(rf"(?<!{WORDCHAR}){re.escape(name)}(?!{WORDCHAR})", low):
            return code
    return None


def my_reports(conn, user_id) -> list[dict]:
    rows = conn.execute("SELECT id, category, created_at FROM report WHERE user_id = %s"
                        " ORDER BY created_at DESC LIMIT 5", (user_id,)).fetchall()
    return [{"id": str(rid), "label": f"{categories().get(cat or '', {}).get('name_en', 'Report')}"
             f" ({at.date().isoformat()})"} for rid, cat, at in rows]


def where_is(conn, question: str, chosen: str | None, previous: dict | None, lat, lng):
    """The state code for a home-chat question: named in it -> the state button -> the
    thread's place for a follow-up on the same kind of issue -> GPS. A national matter (RTI,
    a highway) named without a place is answered for all of India (B-068)."""
    code = place_in(question) or chosen
    scope = slot_rules.scope_of(question)
    if not code and scope in ("rti", "national_highways", "central_ministry_matters"):
        return "IN"
    if not code and previous and scope in (None, previous.get("scope")):
        code = previous.get("state")
    if not code and lat is not None and lng is not None:
        if in_delhi(conn, lat, lng):
            return "DL"
        found = find_state(conn, lat, lng)
        code = found[0] if found else None
    return code


@router.post("/chat")
def chat(body: ChatIn, request: Request, user_id: User):
    parsed = parse(body.message, body.client_msg_id)
    chosen = (body.state_code or "").upper() or None
    if not parsed or (chosen and chosen != "IN" and chosen not in states()):
        return error(422, "validation", "invalid_question")
    question, msg_id = parsed
    thread = Thread("chat", user_id)
    early, call_id = begin(thread, user_id, None, question, msg_id)
    if early is not None:
        return early if isinstance(early, Response) else respond(request, early)
    with connect() as conn:
        history = thread.history(conn, 4)
        previous = history[-1].get("slots") if history else None
        code = where_is(conn, question, chosen, previous, body.lat, body.lng)
        slots = slot_rules.extract(conn, question, None if code == "IN" else code, previous)
        code = code or slots["state"]  # a town we know names its state
        reports = my_reports(conn, user_id)
    ctx = {"endpoint": "chat", "question": question, "force_intent": body.force_intent,
           "trace_seed": f"chat-{msg_id}", "delhi": code == "DL", "state_code": code,
           "state_name": "All India" if code == "IN" else states().get(code or "", {}).get(
               "state_name"), "slots": slots, "facts": [], "department": None,
           "allowed": None, "my_reports": reports, "history": answered(history)}
    return respond(request, run(thread, ctx, call_id, msg_id))


@router.get("/chat")
def chat_history(user_id: User):
    with connect() as conn:
        rows = conn.execute("SELECT id, role, kind, payload, at FROM chat_event"
                            " WHERE user_id = %s ORDER BY id DESC LIMIT 300",
                            (user_id,)).fetchall()
    rows = [(i, role, kind, opened(p), at) for i, role, kind, p, at in reversed(rows)]
    return {"messages": messages(rows)[-50:]}


@router.delete("/chat", status_code=204)
def delete_chat(user_id: User):
    """Delete my home-chat history (REVIEW R-09). The hidden budget rows stay, so deleting
    can't reset the daily question limit."""
    with connect() as conn:
        conn.execute("DELETE FROM chat_event WHERE user_id = %s AND kind <> 'model_call'",
                     (user_id,))
    return Response(status_code=204)


# ---------- feedback ----------

def feedback(thread: Thread, owner_sql: str, owner_args: tuple, message_id: int,
             body: FeedbackIn):
    """The last vote wins. A repeat of the same vote writes nothing (REVIEW B-03)."""
    kind = f"{thread.endpoint}_feedback"
    with connect() as conn:
        row = conn.execute(owner_sql, (message_id, *owner_args)).fetchone()
        if not row:
            return error(404, "not_found", "message_not_found")
        last = conn.execute(f"SELECT payload->>'vote' FROM {thread.table} WHERE {thread.col} = %s"
                            " AND kind = %s AND (payload->>'message_id')::bigint = %s"
                            " ORDER BY id DESC LIMIT 1",
                            (thread.owner, kind, message_id)).fetchone()
        if last and last[0] == body.vote:
            return Response(status_code=204)
        thread.insert(conn, "user", kind,
                      {"message_id": message_id, "vote": body.vote, "reason": body.reason})
    tracing.score(row[0] or "", body.vote, body.reason)
    return Response(status_code=204)


@router.post("/reports/{report_id}/ask/{message_id}/feedback", status_code=204)
def ask_feedback(report_id: uuid.UUID, message_id: int, body: FeedbackIn, user_id: User):
    return feedback(Thread("ask", report_id),
                    "SELECT e.payload->>'trace_seed' FROM report_event e JOIN report r"
                    " ON r.id = e.report_id WHERE e.id = %s AND e.report_id = %s"
                    " AND r.user_id = %s AND e.kind = 'ask_answer'",
                    (report_id, user_id), message_id, body)


@router.post("/chat/{message_id}/feedback", status_code=204)
def chat_feedback(message_id: int, body: FeedbackIn, user_id: User):
    return feedback(Thread("chat", user_id),
                    "SELECT payload->>'trace_seed' FROM chat_event WHERE id = %s"
                    " AND user_id = %s AND kind = 'chat_answer'", (user_id,), message_id, body)
