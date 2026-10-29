"""A tiny stand-in for the backend, used when FAKE_API=1 (tests, QA, backend not ready).

httpx lets us swap the real network for a function (`httpx.MockTransport`), so the UI's
real request code still runs; only the server's answers are canned. Shapes follow
docs/contracts/sprint2-api.md and sprint3-api.md.

Handy triggers for tests and QA:
- a pin outside the box below (e.g. the "India Gate" landmark) → out of coverage
- a pin outside the rough Delhi box (e.g. Mumbai) → out of coverage, outside_delhi
- report text containing "#429" → 429 rate_limited_daily
- landmark text "nothing" → no candidates; "#502" → geocode_failed
- report text containing "#withheld" → the dossier photo is withheld (media 404)
"""

import json
import re

import httpx

TOKEN = "fake-token"
DOSSIER_ID = "00000000-0000-0000-0000-00000000d055"
COVERED = (28.48, 28.60, 77.25, 77.36)  # lat min/max, lng min/max: "our" wards in SE Delhi
DELHI = (28.40, 28.88, 76.84, 77.35)  # a rough Delhi box (the real API uses an outline)

DOSSIER = {
    "id": DOSSIER_ID,
    "category": "pothole",
    "severity": "high",
    "pin": {"lat": 28.544, "lng": 77.311, "ward_no": 185, "ward_name": "Madanpur Khadar East"},
    "department": {
        "code": "MCD",
        "name_en": "Municipal Corporation of Delhi",
        "name_hi": "दिल्ली नगर निगम",
    },
    "routing": {"confidence_label": "very_likely", "curated": True, "fake": True},
    "complaint": {
        "hi": "सेवा में, दिल्ली नगर निगम।\nमदनपुर खादर ईस्ट (वार्ड 185) में सड़क पर बड़ा गड्ढा है, "
        "जिससे दुर्घटना का ख़तरा है। कृपया 15 दिनों में मरम्मत करवाएँ।",
        "en": "To, Municipal Corporation of Delhi.\nThere is a large pothole on the road in "
        "Madanpur Khadar East (Ward 185) that can cause accidents. "
        "Please get it repaired within 15 days.",
    },
    "claims": [],
    "deadline": {"sla_days": 15, "label": "suggested"},
    "reporter_count": 1,
    "channels": [
        {"type": "helpline", "label": "MCD 311", "value": "155305", "deep_link": "tel:155305"}
    ],
    "photo_url": None,  # set per report below
    "citizen_text": "",
}
MEDIA_URL = f"/api/v1/media/{DOSSIER_ID}"
# The last report's text and photo. The real server serves a blurred copy; the fake just
# serves the upload back. Text "#withheld" = blurring failed, so the photo is withheld.
LAST = {"text": "", "photo": None}

CANDIDATES = [
    {"lat": 28.5440, "lng": 77.3110, "label": "Govt School, Madanpur Khadar", "source": "fake"},
    {"lat": 28.5287, "lng": 77.2881, "label": "Sarita Vihar Metro Station", "source": "fake"},
    {"lat": 28.6129, "lng": 77.2295, "label": "India Gate (not covered)", "source": "fake"},
]

# "My issues": one old report, plus every report made while the app runs (newest first).
REPORTS = [{
    "report_id": "00000000-0000-0000-0000-0000000000a0", "state": "dossier_ready",
    "category": "pothole", "ward_no": 185, "department_code": "MCD",
    "created_at": "2026-10-26T09:30:00+05:30", "dossier_id": DOSSIER_ID,
}]


def error(status: int, code: str, key: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message_key": key}})


def field(body: bytes, name: str) -> str | None:
    """Value of one plain multipart field, e.g. lat. Good enough for a fake."""
    m = re.search(rb'name="' + name.encode() + rb'"\r\n\r\n([^\r]*)', body)
    return m.group(1).decode() if m else None


def create_report(body: bytes) -> httpx.Response:
    if b'name="photo"' not in body and b'name="text"' not in body:
        return error(422, "validation", "need_photo_or_text")
    if field(body, "lat") is None or field(body, "lng") is None:
        return error(422, "validation", "need_pin")
    if b"#429" in body:
        return error(429, "rate_limited", "rate_limited_daily")
    lat, lng = float(field(body, "lat")), float(field(body, "lng"))
    acc = field(body, "accuracy_m")
    notices = ["fake_ai"]
    if acc and float(acc) > 50:
        notices.append("low_gps_accuracy")
    if b'name="photo"' in body:
        claim = field(body, "location_verified") == "true"
        notices.append("location_reported_by_phone" if claim else "photo_unverified_location")
    LAST["text"] = field(body, "text") or ""
    # The photo part's bytes: after its headers' blank line, up to the next boundary.
    m = re.search(rb'name="photo".*?\r\n\r\n(.*)\r\n--', body, re.DOTALL)
    LAST["photo"] = m.group(1) if m else None
    covered = COVERED[0] <= lat <= COVERED[1] and COVERED[2] <= lng <= COVERED[3]
    in_delhi = DELHI[0] <= lat <= DELHI[1] and DELHI[2] <= lng <= DELHI[3]
    n = len(REPORTS)
    report = {
        "report_id": f"00000000-0000-0000-0000-0000000000{n:02x}",
        "state": "dossier_ready" if covered else "out_of_coverage",
        "next": None if covered else {"kind": "general_channels", "outside_delhi": not in_delhi,
                                      "channels": DOSSIER["channels"] if in_delhi else []},
        "master_id": "00000000-0000-0000-0000-0000000000b1" if covered else None,
        "dossier_id": DOSSIER_ID if covered else None,
        "notices": notices,
    }
    REPORTS.insert(0, {
        "report_id": report["report_id"], "state": report["state"], "category": "pothole",
        "ward_no": 185 if covered else None, "department_code": "MCD" if covered else None,
        "created_at": "2026-10-28T10:00:00+05:30", "dossier_id": report["dossier_id"],
    })
    return httpx.Response(201, json=report)


def handler(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if path == "/healthz":
        return httpx.Response(200, json={"ok": True})
    if method == "POST" and path == "/api/v1/auth/dev-login":
        name = json.loads(request.read())["name"]
        return httpx.Response(200, json={"token": TOKEN, "user": {"id": "u1", "name": name}})
    if request.headers.get("Authorization") != f"Bearer {TOKEN}":
        return error(401, "unauthorized", "login_required")
    if method == "POST" and path == "/api/v1/reports":
        return create_report(request.read())
    if method == "POST" and path == "/api/v1/geocode":
        text = json.loads(request.read())["text"]
        if "#502" in text:
            return error(502, "geocode_failed", "geocode_failed")
        return httpx.Response(200, json={"candidates": [] if "nothing" in text else CANDIDATES})
    if method == "GET" and path == "/api/v1/me":
        return httpx.Response(200, json={"user": {"id": "u1", "name": "Priya"}})
    if method == "GET" and path == "/api/v1/me/reports":
        return httpx.Response(200, json={"reports": REPORTS})
    if method == "GET" and path == f"/api/v1/dossiers/{DOSSIER_ID}":
        d = {**DOSSIER, "citizen_text": LAST["text"],
             "photo_url": MEDIA_URL if LAST["photo"] else None}
        return httpx.Response(200, content=json.dumps(d, ensure_ascii=False).encode())
    if method == "GET" and path == MEDIA_URL and LAST["photo"] and "#withheld" not in LAST["text"]:
        return httpx.Response(200, content=LAST["photo"], headers={"content-type": "image/png"})
    return error(404, "not_found", "not_found")


transport = httpx.MockTransport(handler)
