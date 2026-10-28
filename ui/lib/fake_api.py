"""A tiny stand-in for the backend, used when FAKE_API=1 (tests, QA, backend not ready).

httpx lets us swap the real network for a function (`httpx.MockTransport`), so the UI's
real request code still runs; only the server's answers are canned. Shapes follow
docs/contracts/sprint2-api.md.
"""

import json

import httpx

DOSSIER_ID = "00000000-0000-0000-0000-00000000d055"

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
    "photo_url": f"/api/v1/media/{DOSSIER_ID}",
}

OUT_OF_COVERAGE = {
    "report_id": "00000000-0000-0000-0000-0000000000a2",
    "state": "out_of_coverage",
    "next": {"kind": "general_channels", "channels": DOSSIER["channels"]},
    "master_id": None,
    "dossier_id": None,
    "notices": ["fake_ai"],
}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/healthz":
        return httpx.Response(200, json={"ok": True})
    if request.method == "POST" and path == "/api/v1/reports":
        # The UI only adds a form field when it has a value, so a name check is enough here.
        body = request.read()
        if b'name="photo"' not in body and b'name="text"' not in body:
            error = {"error": {"code": "validation", "message_key": "need_photo_or_text"}}
            return httpx.Response(422, json=error)
        if b"uncovered" in body:  # lets tests and QA reach the out-of-coverage screen
            return httpx.Response(201, json=OUT_OF_COVERAGE)
        return httpx.Response(201, json={
            "report_id": "00000000-0000-0000-0000-0000000000a1",
            "state": "dossier_ready",
            "next": None,
            "master_id": "00000000-0000-0000-0000-0000000000b1",
            "dossier_id": DOSSIER_ID,
            "notices": ["fake_ai"],
        })
    if request.method == "GET" and path == f"/api/v1/dossiers/{DOSSIER_ID}":
        return httpx.Response(200, content=json.dumps(DOSSIER, ensure_ascii=False).encode())
    return httpx.Response(404, json={"error": {"code": "not_found", "message_key": "not_found"}})


transport = httpx.MockTransport(handler)
