"""The only place the UI talks to the backend.

Contracts: docs/contracts/sprint2-api.md … sprint6-api.md. Every call except health and
login takes the user's token and sends it as `Authorization: Bearer <token>`.
"""

import json
import os
from collections.abc import Iterator
from itertools import chain

import httpx

from strings import TEXT

# 127.0.0.1, not localhost: uvicorn listens on IPv4 only, and Windows tries IPv6 (::1) first,
# which costs ~2 s per call before it falls back.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8010")
# A report can wait for the models: the API keeps each model call inside ~50 s (B-031).
TIMEOUT_S = 90


class ApiError(Exception):
    """Carries a message_key so the page can show friendly text from strings.py, and the
    seconds to wait when the server says so (`retry_after`, a busy/throttled 429)."""

    def __init__(self, message_key: str, retry_after: float | None = None):
        super().__init__(message_key)
        self.message_key = message_key
        self.retry_after = retry_after


def _seconds(value) -> float | None:
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _client(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if os.getenv("FAKE_API") == "1":
        from lib.fake_api import transport  # canned answers, no server needed

        return httpx.Client(base_url=API_URL, transport=transport, headers=headers,
                            timeout=TIMEOUT_S)
    return httpx.Client(base_url=API_URL, headers=headers, timeout=TIMEOUT_S)


def _check(resp: httpx.Response) -> dict:
    if resp.is_error:
        wait = _seconds(resp.headers.get("Retry-After"))
        try:
            err = resp.json()["error"]
            key = err["message_key"]
            wait = _seconds(err.get("retry_after")) if "retry_after" in err else wait
        except (ValueError, KeyError, TypeError):  # not our error shape (e.g. a plain 500)
            key = "server_error"
        # A key the UI has no text for gets the generic message, never the raw key.
        raise ApiError(key if key in TEXT else "server_error", wait)
    return resp.json()


def _call(method: str, path: str, token: str | None = None, pick: str | None = None,
          **kwargs):
    """One request + error handling, shared by every endpoint below. `pick` = the one field
    of the answer the caller wants. Anything unexpected (bad JSON, a missing field) becomes
    the generic "server_error" message, never a traceback on the page (B-030)."""
    try:
        with _client(token) as c:
            body = _check(c.request(method, f"/api/v1{path}", **kwargs))
        return body[pick] if pick else body
    except httpx.HTTPError:  # server down, timeout, DNS
        raise ApiError("api_unreachable") from None
    except (ValueError, KeyError, TypeError):  # not the JSON shape the contract promises
        raise ApiError("server_error") from None


def health(timeout: float = 3) -> bool:
    try:
        with _client() as c:
            return c.get("/healthz", params={"db": 1}, timeout=timeout).json()["ok"]
    except (httpx.HTTPError, ValueError, KeyError):  # no server, bad JSON, wrong shape
        return False


def dev_login(name: str, email: str) -> dict:
    """Returns {"token": ..., "user": {"id", "name"}}."""
    body = {"name": name, "email": email} if email else {"name": name}
    return _call("POST", "/auth/dev-login", json=body)


def sign_up(name: str, email: str, password: str, captcha: str) -> dict:
    """AUTH_MODE=supabase: create an account. `captcha` = the Turnstile token.
    Returns {"token", "user": {"id", "name"}} (same as dev_login)."""
    return _call("POST", "/auth/signup", json={"name": name, "email": email,
                                               "password": password, "captcha": captcha})


def sign_in(email: str, password: str, captcha: str) -> dict:
    """AUTH_MODE=supabase: sign in with email + password. Same answer shape as sign_up."""
    return _call("POST", "/auth/login",
                 json={"email": email, "password": password, "captcha": captcha})


def me(token: str) -> dict:
    """{"id", "name"} of the token's user. Checks a token kept across a refresh."""
    return _call("GET", "/me", token, pick="user")


def create_report(token: str, text: str, photo: tuple[str, bytes, str] | None, pin: dict,
                  verified: bool) -> dict:
    """POST /reports as multipart. `photo` is (filename, bytes, mime type) or None.

    `pin` is {"lat", "lng", "source", "acc"} from the pin-confirm card (acc may be None).
    """
    # Every field goes in `files` so httpx always sends multipart/form-data. With `data=` and
    # no file, httpx would quietly switch to url-encoded. (None, value) = a plain text field.
    parts = {
        "ui_lang": (None, "en"),  # the UI is English only (UX §14)
        "lat": (None, str(pin["lat"])),
        "lng": (None, str(pin["lng"])),
        "pin_source": (None, pin["source"]),
        "location_verified": (None, "true" if verified else "false"),
    }
    if pin.get("acc") is not None:
        parts["accuracy_m"] = (None, str(round(pin["acc"], 1)))
    if text:
        parts["text"] = (None, text)
    if photo:
        parts["photo"] = photo
    return _call("POST", "/reports", token, files=parts)


def get_dossier(token: str, dossier_id: str) -> dict:
    return _call("GET", f"/dossiers/{dossier_id}", token)


def get_photo(token: str, photo_url: str) -> bytes | None:
    """The dossier's (blurred) photo, or None if it is missing. Used when reopening a report."""
    try:
        with _client(token) as c:
            resp = c.get(photo_url)
            return resp.content if resp.is_success else None
    except httpx.HTTPError:
        return None


def geocode(token: str, text: str) -> list[dict]:
    """Up to 5 places for a landmark: [{"lat", "lng", "label", "source"}]."""
    return _call("POST", "/geocode", token, pick="candidates", json={"text": text})


def my_reports(token: str) -> list[dict]:
    return _call("GET", "/me/reports", token, pick="reports")


def answer(token: str, report_id: str, body: dict) -> dict:
    """Answer the report's pending question: {"value": "inner_lane"} (a quick reply) or
    {"text": "bses wala"} (free text, any language). Same envelope as POST /reports."""
    return _call("POST", f"/reports/{report_id}/answer", token, json=body)


def conversation(token: str, report_id: str) -> list[dict]:
    """The report's chat thread, oldest first: [{"role", "kind", "text", ...}]."""
    return _call("GET", f"/reports/{report_id}/conversation", token, pick="messages")


def categories(token: str) -> list[dict]:
    """Every category the backend knows (data/categories.csv): [{"code", "name_en"}, ...]."""
    return _call("GET", "/categories", token, pick="categories")


# ---------- Sprint 6: questions (sprint6-api.md §4, §4A) ----------

def _stream(path: str, token: str, body: dict) -> Iterator[tuple[str, dict]]:
    """POST with `Accept: text/event-stream` and yield (event, data) as each event arrives:
    "step" {"key"}, "sentence" {"text", "cites", "sources"}, "done" {the full answer}.
    An "error" event (both AIs failed mid-way) and errors before the stream starts (401,
    404, 409, 422, 429, 503) raise ApiError. A server that answers plain JSON instead
    yields just ("done", body)."""
    try:
        with _client(token) as c, c.stream("POST", f"/api/v1{path}", json=body,
                                           headers={"Accept": "text/event-stream"}) as resp:
            if resp.is_error:
                resp.read()
                _check(resp)
            if not resp.headers.get("content-type", "").startswith("text/event-stream"):
                resp.read()
                yield "done", resp.json()
                return
            event, data = "message", []
            for line in chain(resp.iter_lines(), [""]):  # "" ends the last event too
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:  # a blank line ends one event
                    payload = json.loads("\n".join(data))
                    if event == "error":
                        key = payload.get("message_key", "qa_unavailable")
                        raise ApiError(key if key in TEXT else "server_error",
                                       _seconds(payload.get("retry_after")))
                    yield event, payload
                    event, data = "message", []
    except httpx.HTTPError:
        raise ApiError("api_unreachable") from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ApiError("server_error") from None


def chat_stream(token: str, body: dict) -> Iterator[tuple[str, dict]]:
    """Home-page assistant. body = {"message", "client_msg_id", "lat"?, "lng"?,
    "state_code"?, "force_intent"?}."""
    return _stream("/chat", token, body)


def ask_stream(token: str, report_id: str, body: dict) -> Iterator[tuple[str, dict]]:
    """A question about one report. body = {"question", "client_msg_id", "force_intent"?}."""
    return _stream(f"/reports/{report_id}/ask", token, body)


def chat_history(token: str) -> list[dict]:
    """The home chat's last 50 messages, oldest first (kinds chat / chat_answer)."""
    return _call("GET", "/chat", token, pick="messages")


def _feedback(path: str, token: str, vote: str) -> None:
    try:
        with _client(token) as c:
            resp = c.post(f"/api/v1{path}", json={"vote": vote})
    except httpx.HTTPError:
        raise ApiError("api_unreachable") from None
    if resp.is_error:  # 204 has no body, so not _call
        _check(resp)


def chat_feedback(token: str, message_id: int, vote: str) -> None:
    _feedback(f"/chat/{message_id}/feedback", token, vote)


def ask_feedback(token: str, report_id: str, message_id: int, vote: str) -> None:
    _feedback(f"/reports/{report_id}/ask/{message_id}/feedback", token, vote)
