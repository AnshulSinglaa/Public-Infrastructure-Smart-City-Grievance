"""A tiny stand-in for the backend, used when FAKE_API=1 (tests, QA, backend not ready).

httpx lets us swap the real network for a function (`httpx.MockTransport`), so the UI's
real request code still runs; only the server's answers are canned. Shapes follow
docs/contracts/sprint2-api.md … sprint5-api.md.

Every report keeps a chat thread (GET /reports/{id}/conversation), and questions are
answered with POST /reports/{id}/answer, like the real API. As in the real transcript,
emergency / out-of-coverage messages carry their buttons as `options`, out-of-coverage
also `outside_delhi`, and rejected the retake `reason`.

Handy triggers for tests and QA (in the report text):
- a pin outside the box below (e.g. the "India Gate" landmark) → out of coverage
- a pin outside the rough Delhi box (e.g. Mumbai) → outside Delhi: Maharashtra portal;
  add "#unverified" → CPGRAMS only
- "#429" → 429 rate_limited_daily (in a typed answer: rate_limited_answers) · "#503" → 503
  ai_unavailable
- landmark text "nothing" → no candidates; "#502" → geocode_failed
- "#withheld" → blurring failed: the dossier photo is withheld
- "#photo_down" → the vision model was down: "photo_not_checked"
- "#clarify" / "#mismatch" → category question (low confidence / photo and text disagree)
- "#road_q", "#drain_q", "#discom_q", "#ghat_q" → that issue question; "#pick" → two
  departments to choose from; "#unclear" → a road question whose first answer is re-asked
- "#junk_selfie" (or _meme, _screenshot, _blank, _unrelated) → rejected_junk
- "#emergency" → a "call first" emergency message comes first

Questions (sprint6-api.md §4, §4A: POST /chat, POST /reports/{id}/ask, both streamed):
- "hi" / "hello" / "thanks" → greeting; "cricket", "movie", "recipe" → off_topic;
  "gaddha" / "pothole" / "garbage" → new_problem (Start a report); "my complaint" → pick_report
  (/chat); "ignore previous" → blocked
- /chat: a place in the question ("Delhi", "Jaipur", "Mumbai"), else state_code, else lat/lng,
  else needs_location. Jaipur / Mumbai → national answer + "I don't know the local rule"
- "Badhra" / "Haryana" → the structured answer (FIX-PLAN-S6 A): responsible (general rule,
  no citation) → where (CM Window) → steps → escalation, plus Start a report for "my house"
  (F); a follow-up without a place keeps the last state ("step by step how to report this")
- no reports at all (REPORTS empty) + "my complaint" → chat_no_reports + Start a report
- "#throttle" → 429 qa_throttled with retry_after 2 (body and Retry-After header)
- "#notfound" → not_found · "#429" → 429 rate_limited_questions · "#busy" → 429 qa_busy ·
  "#503" → 503 qa_unavailable before the stream · "#down" → an error event mid-stream
Free-text answers match an option when they contain its value or a word of its label
("bses wala" → BSES Rajdhani); anything else is "unclear" and the question is asked again.
"""

import json
import re

import httpx

TOKEN = "fake-token"
DOSSIER_ID = "00000000-0000-0000-0000-00000000d055"
COVERED = (28.48, 28.60, 77.25, 77.36)  # lat min/max, lng min/max: "our" wards in SE Delhi
DELHI = (28.40, 28.88, 76.84, 77.35)  # a rough Delhi box (the real API uses an outline)
AT = "2026-10-30T10:00:00+05:30"

DOSSIER = {
    "id": DOSSIER_ID,
    "category": "POTHOLE",
    "severity": "high",
    "pin": {"lat": 28.544, "lng": 77.311, "ward_no": 185, "ward_name": "Madanpur Khadar East"},
    "department": {
        "code": "MCD",
        "name_en": "Municipal Corporation of Delhi",
        "name_hi": "दिल्ली नगर निगम",
    },
    # As the real API for a pothole on a lane (RD-05, width from the map). After an answer,
    # decided() below changes it the way the real API does (B-049).
    "routing": {"confidence_label": "very_likely", "curated": True, "fake": True,
                "rule_id": "RD-05", "evidence": ["width"], "why_key": "why_width",
                "why": "The road is narrower than 60 ft, so Municipal Corporation of Delhi "
                "looks after it"},
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
LAST = {"text": "", "photo": None, "decided": {}}
DEPARTMENTS = {
    "MCD": DOSSIER["department"],
    "PWD": {"code": "PWD", "name_en": "Public Works Department (GNCTD)",
            "name_hi": "लोक निर्माण विभाग"},
    "NHAI": {"code": "NHAI", "name_en": "National Highways Authority of India",
             "name_hi": "भारतीय राष्ट्रीय राजमार्ग प्राधिकरण"},
}


def decided(qid: str, value: str) -> dict:
    """The dossier's department + routing after an answer, as the real API makes them:
    an issue answer -> evidence user_answer; a pick -> that department, 'You chose …'."""
    routing = {**DOSSIER["routing"], "evidence": ["user_answer"]}
    if qid == "pick_candidate":
        dept = DEPARTMENTS[value]
        why = next(o["why"] for o in PICK["options"] if o["value"] == value)
        why = "It" + why[len(dept["name_en"]):] if why.startswith(dept["name_en"]) else why
        return {"department": dept, "routing": {
            **routing, "rule_id": "RD-02", "why_key": "why_user_answer",
            "confidence_label": "likely", "why": f"You chose {dept['name_en']}. {why}"}}
    if qid == "clarify_category":
        return {}  # the category answer: the lane decides, as without a question
    return {"routing": {**routing, "why_key": "why_rule",
                        "why": "Municipal Corporation of Delhi handles pothole here (rule RD-05)"}}

CANDIDATES = [
    {"lat": 28.5440, "lng": 77.3110, "label": "Govt School, Madanpur Khadar", "source": "fake"},
    {"lat": 28.5287, "lng": 77.2881, "label": "Sarita Vihar Metro Station", "source": "fake"},
    {"lat": 28.6129, "lng": 77.2295, "label": "India Gate (not covered)", "source": "fake"},
    {"lat": 18.9398, "lng": 72.8355, "label": "CST station, Mumbai (outside Delhi)",
     "source": "fake"},
]
NOT_SURE = {"value": "not_sure", "label": "Not sure"}
# The questions the routing graph can pause on (sprint5-api.md §2).
QUESTIONS = {
    "#road_q": ("road_class", "Is this on a main road or an inner lane?", [
        {"value": "main_road", "label": "Main road"},
        {"value": "inner_lane", "label": "Inner lane / gali"}, NOT_SURE]),
    "#drain_q": ("drain_type", "Is it a big open nala or a sewer line / manhole?", [
        {"value": "open_nala", "label": "Open nala"},
        {"value": "sewer_manhole", "label": "Sewer line / manhole"}, NOT_SURE]),
    "#discom_q": ("which_discom", "Which company sends your electricity bill?", [
        {"value": "brpl", "label": "BSES Rajdhani (BRPL)"},
        {"value": "bypl", "label": "BSES Yamuna (BYPL)"},
        {"value": "tpddl", "label": "Tata Power (TPDDL)"}, NOT_SURE]),
    "#ghat_q": ("ghat_position", "Is it on the river bank or in the water?", [
        {"value": "river_bank", "label": "River bank"},
        {"value": "in_water", "label": "In the water"}, NOT_SURE]),
}
PICK = {"role": "assistant", "kind": "question", "question_id": "pick_candidate",
        "text": "More than one department could handle this. Which fits?", "options": [
            {"value": "PWD", "label": "Public Works Department (GNCTD)",
             "confidence_label": "likely",
             "why": "Mathura Road is on the PWD list of roads, so Public Works Department "
             "(GNCTD) maintains it"},
            {"value": "NHAI", "label": "National Highways Authority of India",
             "confidence_label": "possible",
             "why": "National Highways Authority of India may also be responsible here "
             "(rule RD-02)"}]}
CLARIFY = ("We're not sure — which is it?", [
    {"value": "POTHOLE", "label": "Pothole · likely"},
    {"value": "WATERLOGGING", "label": "Waterlogging · maybe"}])
MISMATCH = (("Your photo looks like garbage dump, your text says streetlight not working — "
            "which is right?"), [
                {"value": "GARBAGE_DUMP", "label": "Garbage dump · likely"},
                {"value": "STREETLIGHT_OUT", "label": "Streetlight not working · maybe"}])
EMERGENCY = [{"label": "Emergency 112", "value": "112", "deep_link": "tel:112"},
             {"label": "BSES Rajdhani", "value": "19123", "deep_link": "tel:19123"}]
# A few rows of data/categories.csv (the real list has 37). Codes are UPPERCASE.
CATEGORIES = [
    {"code": "POTHOLE", "name_en": "Pothole", "group": "roads", "emergency": False},
    {"code": "GARBAGE_DUMP", "name_en": "Garbage dump", "group": "waste", "emergency": False},
    {"code": "WATERLOGGING", "name_en": "Waterlogging", "group": "water", "emergency": False},
    {"code": "STREETLIGHT_OUT", "name_en": "Streetlight not working", "group": "power",
     "emergency": False},
    {"code": "LIVE_WIRE", "name_en": "Live wire", "group": "power", "emergency": True},
    {"code": "DEAD_ANIMAL", "name_en": "Dead animal", "group": "health", "emergency": False},
    {"code": "OTHER", "name_en": "Other", "group": "other", "emergency": False},
]

DONE = {"role": "assistant", "kind": "dossier", "text": "Your complaint is ready.",
        "dossier_id": DOSSIER_ID, "at": AT}
OLD_ID = "00000000-0000-0000-0000-0000000000a0"
# "My reports": one old report, plus every report made while the app runs (newest first).
REPORTS = [{
    "report_id": OLD_ID, "state": "dossier_ready",
    "category": "POTHOLE", "ward_no": 185, "department_code": "MCD",
    "created_at": "2026-10-26T09:30:00+05:30", "dossier_id": DOSSIER_ID,
}]
CONV = {OLD_ID: [{"role": "user", "kind": "report", "text": "Sadak pe bada gaddha hai",
                  "at": AT}, DONE]}  # report_id → its chat thread
PENDING = {}  # report_id → {"question": msg, "unclear_once": bool} while a question waits


def row(report_id, state, category, covered, dossier_id) -> dict:
    """One "My reports" row, the shape GET /me/reports sends."""
    return {"report_id": report_id, "state": state, "category": category,
            "ward_no": 185 if covered else None, "department_code": "MCD" if covered else None,
            "created_at": "2026-10-28T10:00:00+05:30", "dossier_id": dossier_id}


def error(status: int, code: str, key: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message_key": key}})


def field(body: bytes, name: str) -> str | None:
    """Value of one plain multipart field, e.g. lat. Good enough for a fake."""
    m = re.search(rb'name="' + name.encode() + rb'"\r\n\r\n([^\r]*)', body)
    return m.group(1).decode() if m else None


def say(report_id: str, **msg) -> None:
    CONV[report_id].append({"role": "assistant", "at": AT, **msg})


def envelope(report_id: str, state: str, notices=(), dossier_id=None, nxt=None) -> dict:
    return {"report_id": report_id, "state": state, "next": nxt, "notices": list(notices),
            "master_id": None, "dossier_id": dossier_id}


# The assistant's text for these steps, as in backend/app/intake/events.py.
SAY_OOC = "We don't cover this area yet. Here is where you can complain."
SAY_REJECTED = "This photo doesn't show a civic problem. Please take another photo."


def create_report(body: bytes) -> httpx.Response:
    if b'name="photo"' not in body and b'name="text"' not in body:
        return error(422, "validation", "need_photo_or_text")
    if field(body, "lat") is None or field(body, "lng") is None:
        return error(422, "validation", "need_pin")
    if b"#429" in body:
        return error(429, "rate_limited", "rate_limited_daily")
    if b"#503" in body:
        return error(503, "ai_unavailable", "ai_unavailable")
    lat, lng = float(field(body, "lat")), float(field(body, "lng"))
    acc = field(body, "accuracy_m")
    notices = ["fake_ai"]
    if acc and float(acc) > 50:
        notices.append("low_gps_accuracy")
    if b'name="photo"' in body:
        claim = field(body, "location_verified") == "true"
        notices.append("location_reported_by_phone" if claim else "photo_unverified_location")
    if b"#photo_down" in body:
        notices.append("photo_not_checked")
    text = field(body, "text") or ""
    LAST["text"], LAST["decided"] = text, {}
    # The photo part's bytes: after its headers' blank line, up to the next boundary.
    m = re.search(rb'name="photo".*?\r\n\r\n(.*)\r\n--', body, re.DOTALL)
    LAST["photo"] = m.group(1) if m else None
    covered = COVERED[0] <= lat <= COVERED[1] and COVERED[2] <= lng <= COVERED[3]
    in_delhi = DELHI[0] <= lat <= DELHI[1] and DELHI[2] <= lng <= DELHI[3]

    rid = f"00000000-0000-0000-0000-0000000000{len(REPORTS):02x}"
    shown = re.sub(r"\s*#\w+", "", text)  # the QA switches stay out of the chat bubble
    CONV[rid] = [{"role": "user", "kind": "report", "text": shown, "at": AT}]
    if b"#emergency" in body:  # first, above everything; it never blocks the report
        numbers = ", ".join(f"{c['label']} {c['value']}" for c in EMERGENCY)
        say(rid, kind="emergency", text=f"This looks dangerous. Call first: {numbers}.",
            options=EMERGENCY)

    junk = re.search(rb"#junk_(\w+)", body)
    if junk:  # saved with no category, like the real API (B-029)
        reason = junk.group(1).decode()
        say(rid, kind="rejected", text=SAY_REJECTED, reason=reason)
        REPORTS.insert(0, row(rid, "rejected_junk", None, None, None))
        return httpx.Response(201, json=envelope(rid, "rejected_junk", notices,
                                                 nxt={"kind": "retake_photo", "reason": reason}))
    if not covered:
        REPORTS.insert(0, row(rid, "out_of_coverage", "POTHOLE", False, None))
        report = envelope(rid, "out_of_coverage", notices, nxt={
            "kind": "general_channels", "outside_delhi": not in_delhi,
            "channels": DOSSIER["channels"] if in_delhi else []})
        if not in_delhi:  # TOP level of the response, as the real API (B-034)
            report["state_directory"] = {
                "state": "Maharashtra", "portal_name": "Aaple Sarkar",
                "portal_url": "https://grievances.maharashtra.gov.in",
                "helpline": "1800120800", "verified": b"#unverified" not in body,
                "national": {"name": "CPGRAMS", "url": "https://pgportal.gov.in"}}
        say(rid, kind="out_of_coverage", text=SAY_OOC, outside_delhi=not in_delhi,
            options=coverage_options(report))
        if b"#emergency" in body:
            report["emergency"] = {"kind": "live_wire", "call_first": EMERGENCY}
        return httpx.Response(201, json=report)

    question = None
    for switch, (qid, qtext, options) in QUESTIONS.items():
        if switch.encode() in body or (switch == "#road_q" and b"#unclear" in body):
            question = {"kind": "question", "question_id": qid, "text": qtext,
                        "options": options}
    if b"#pick" in body:
        question = dict(PICK)
    if b"#clarify" in body or b"#mismatch" in body:
        qtext, options = MISMATCH if b"#mismatch" in body else CLARIFY
        question = {"kind": "question", "question_id": "clarify_category", "text": qtext,
                    "options": options}
    if question:  # paused: no category yet in My reports (B-029)
        say(rid, **{k: v for k, v in question.items() if k != "role"})
        PENDING[rid] = {"question": question, "unclear_once": b"#unclear" in body}
        REPORTS.insert(0, row(rid, "needs_clarification", None, None, None))
        return httpx.Response(201, json=envelope(rid, "needs_clarification", notices,
                                                 nxt=question))
    CONV[rid].append(DONE)
    REPORTS.insert(0, row(rid, "dossier_ready", "POTHOLE", True, DOSSIER_ID))
    report = envelope(rid, "dossier_ready", notices, DOSSIER_ID)
    if b"#emergency" in body:
        report["emergency"] = {"kind": "live_wire", "call_first": EMERGENCY}
    return httpx.Response(201, json=report)


def coverage_options(report: dict) -> list[dict]:
    """The out-of-coverage buttons, as events.record_out_of_coverage stores them."""
    out = [{"value": c["value"], "label": c["label"], "deep_link": c["deep_link"]}
           for c in report["next"]["channels"]]
    sd = report.get("state_directory")
    if sd:
        if sd["verified"]:
            out.append({"value": sd["portal_url"], "label": sd["portal_name"],
                        "deep_link": sd["portal_url"]})
            out.append({"value": sd["helpline"], "label": f"Call {sd['helpline']}",
                        "deep_link": f"tel:{sd['helpline']}"})
        nat = sd["national"]
        out.append({"value": nat["url"], "label": nat["name"], "deep_link": nat["url"]})
    return out


def match(text: str, options: list[dict]) -> dict | None:
    """Free text → one option, like the text LLM (strict). None = unclear."""
    text = text.lower()
    for opt in options:
        if opt["value"] == "not_sure":
            continue
        words = re.findall(r"[a-z]{4,}", opt["label"].lower()) + [opt["value"].lower()]
        if any(w in text for w in words):
            return opt
    return None


def answer(rid: str, body: dict) -> httpx.Response:
    pending = PENDING.get(rid)
    if not pending:
        return error(409, "wrong_step", "wrong_step")
    q = pending["question"]
    if "#429" in body.get("text", ""):  # SEC-015: too many typed answers
        return error(429, "rate_limited", "rate_limited_answers")
    if "value" in body:
        opt = next((o for o in q["options"] if o["value"] == body["value"]), None)
        if not opt:
            return error(422, "validation", "invalid_answer")
        CONV[rid].append({"role": "user", "kind": "answer", "text": opt["label"], "at": AT})
    else:
        CONV[rid].append({"role": "user", "kind": "answer", "text": body["text"], "at": AT})
        opt = None if pending["unclear_once"] else match(body["text"], q["options"])
        pending["unclear_once"] = False
        if not opt:  # unclear: the same question again
            say(rid, **{**{k: v for k, v in q.items() if k != "role"}, "retry": True})
            return httpx.Response(200, json=envelope(rid, "needs_clarification", nxt=q))
    if opt["value"] == "not_sure" and q["question_id"] != "pick_candidate":
        pending["question"] = dict(PICK)
        say(rid, **{k: v for k, v in PICK.items() if k != "role"})
        return httpx.Response(200, json=envelope(rid, "needs_clarification", nxt=PICK))
    del PENDING[rid]
    LAST["decided"] = decided(q["question_id"], opt["value"])
    CONV[rid].append(DONE)
    for r in REPORTS:
        if r["report_id"] == rid:
            r.update(row(rid, "dossier_ready", "POTHOLE", True, DOSSIER_ID))
    return httpx.Response(200, json=envelope(rid, "dossier_ready", dossier_id=DOSSIER_ID))


# ---------- Sprint 6: questions (sprint6-api.md §4, §4A) ----------
# Quotes and URLs here are illustrative, like the contract's examples.
SRC = {
    "rti_request": {"kind": "document", "doc_title": "Right to Information Act, 2005",
                    "issuer": "Government of India", "doc_type": "act", "tier": 1,
                    "layer": "national", "doc_date": "2005-06-15", "section_ref": "Section 6(1)",
                    "page": None, "quote": "shall make a request in writing or through "
                    "electronic means", "url": "https://rti.gov.in/rti-act.pdf",
                    "fetched_at": "2026-11-12", "stale": False, "link_status": "ok"},
    "rti_days": {"kind": "document", "doc_title": "Right to Information Act, 2005",
                 "issuer": "Government of India", "doc_type": "act", "tier": 1,
                 "layer": "national", "doc_date": "2005-06-15", "section_ref": "Section 7(1)",
                 "page": None, "quote": "within thirty days of the receipt of the request",
                 "url": "https://rti.gov.in/rti-act.pdf", "fetched_at": "2026-11-12",
                 "stale": False, "link_status": "ok"},
    "light": {"kind": "document", "doc_title": "Street lighting", "issuer": "MCD",
              "doc_type": "official_page", "tier": 1, "layer": "delhi", "doc_date": None,
              "section_ref": "Maintenance", "page": None,
              "quote": "street lights on roads below 60 feet are maintained by the MCD",
              "url": "https://mcdonline.nic.in/", "fetched_at": "2026-08-01",
              "stale": True, "link_status": "ok"},
    "cpgrams": {"kind": "document", "doc_title": "CPGRAMS guidelines 2024", "issuer": "DARPG",
                "doc_type": "notification", "tier": 1, "layer": "national",
                "doc_date": "2024-08-23", "section_ref": "Para 4", "page": 3,
                "quote": "grievances should be redressed within 21 days",
                "url": "https://pgportal.gov.in/", "fetched_at": "2026-11-12",
                "stale": False, "link_status": "ok"},
    "cmwindow": {"kind": "document", "doc_title": "CM Window", "issuer": "Government of Haryana",
                 "doc_type": "official_page", "tier": 1, "layer": "state", "doc_date": None,
                 "section_ref": "How to file", "page": None,
                 "quote": "register your grievance online on the CM Window portal",
                 "url": "https://cmharyanacell.nic.in/", "fetched_at": "2026-11-12",
                 "stale": False, "link_status": "ok"},
    "Y1": {"kind": "your_report", "label": "Routing reason",
           "quote": "The road is narrower than 60 ft"},
}
# (keywords, [(sentence, [source keys])]): the first match answers; the last is the default.
ANSWERS = [
    # (text, sources, part): the structured answer for a local issue in a Haryana town
    (("badhra", "haryana", "water clog", "waterlogging", "step by step"), [
        (("Drains and waterlogging in a town are usually handled by the local municipal body: "
         "here, Municipal Committee Badhra."), [], "responsible"),
        ("File the complaint on Haryana's CM Window portal.", ["cmwindow"], "where"),
        ("Register on the CM Window portal with your mobile number.", ["cmwindow"], "step"),
        ("Describe the waterlogging, add the place and a photo, and submit.", ["cmwindow"],
         "step"),
        ("Note the grievance number to track it.", ["cmwindow"], "step"),
        ("If nothing happens, raise it again on CM Window with the grievance number.",
         ["cmwindow"], "escalation")]),
    (("rti", "information"), [
        ("You can ask for information by making a request in writing or online.",
         ["rti_request"]),
        ("The office has to reply within thirty days.", ["rti_days"])]),
    (("streetlight", "street light"), [
        ("In Delhi, street lights on roads below 60 feet are looked after by the MCD.",
         ["light"])]),
    ((), [("A complaint on CPGRAMS should be settled within 21 days.", ["cpgrams"])]),
]
PLACES = {"badhra": "HR", "haryana": "HR", "delhi": "DL", "jaipur": "RJ", "rajasthan": "RJ", "mumbai": "MH",
          "maharashtra": "MH"}
CPGRAMS = {"label": "CPGRAMS", "value": "https://pgportal.gov.in",
           "deep_link": "https://pgportal.gov.in"}
PORTALS = {"HR": {"label": "CM Window", "value": "https://cmharyanacell.nic.in",
                  "deep_link": "https://cmharyanacell.nic.in"},
           "RJ": {"label": "Rajasthan Sampark", "value": "https://sampark.rajasthan.gov.in",
                  "deep_link": "https://sampark.rajasthan.gov.in"},
           "MH": {"label": "Aaple Sarkar", "value": "https://grievances.maharashtra.gov.in",
                  "deep_link": "https://grievances.maharashtra.gov.in"}}
CHAT = []  # the home chat (GET /chat), oldest first
SEEN = {}  # client_msg_id → the stored answer (a resend returns it, nothing new)
NEXT_ID = [4400]


def intent(text: str, home: bool) -> str:
    low = text.lower()
    if re.match(r"\s*(hi|hello|hey|namaste|thanks|thank you)\b", low):
        return "greeting"
    if re.search(r"cricket|movie|recipe|joke", low):
        return "off_domain"
    if "ignore previous" in low:
        return "blocked"
    if re.search(r"gaddha|pothole|garbage|kooda", low):
        return "new_problem"
    if home and re.search(r"my (complaint|report)|meri complaint", low):
        return "my_complaint"
    return "general_civic"


def place(text: str, body: dict) -> str | None:
    """Location order (§4A): a place named in the question → state_code → lat/lng."""
    low = text.lower()
    named = next((code for name, code in PLACES.items() if name in low), None)
    if named or body.get("state_code"):
        return named or body["state_code"]
    if body.get("lat") is not None:
        lat, lng = body["lat"], body["lng"]
        return "DL" if DELHI[0] <= lat <= DELHI[1] and DELHI[2] <= lng <= DELHI[3] else "MH"
    return None


STATE_NAMES = {"HR": "Haryana", "DL": "Delhi", "RJ": "Rajasthan", "MH": "Maharashtra", "IN": "India"}
OWN = re.compile(r"my (house|home|street|lane|gali)|in front of my|mere ghar|hamare ghar")
KEYS = {"off_topic": "qa_off_topic", "blocked": "qa_blocked", "fixed_reply": "chat_greeting",
        "needs_location": "chat_which_state", "not_found": "qa_not_found",
        "out_of_scope": "qa_out_of_scope"}


def qa(text: str, body: dict, report_id: str | None) -> dict:
    """The final answer object (the `done` event, or the plain JSON response), with the
    6b fields: message_key (null when answered), state_code and state_name (/ask too)."""
    out = answer_obj(text, body, report_id)
    out.setdefault("state_code", "DL")  # /ask: the fake's reports are in Delhi
    out["state_name"] = STATE_NAMES.get(out["state_code"])
    picks = any(a["kind"] == "pick_report" for a in out["actions"])
    if out["intent"] == "my_complaint" and out["status"] == "action":
        out["message_key"] = "chat_pick_report" if picks else "chat_no_reports"  # B-073
    elif out["status"] == "action":
        out["message_key"] = "chat_start_report"
    else:
        out["message_key"] = KEYS.get(out["status"])
    return out


def answer_obj(text: str, body: dict, report_id: str | None) -> dict:
    NEXT_ID[0] += 1
    home = report_id is None
    gate = body.get("force_intent") or intent(text, home)
    ask_anyway = {"kind": "ask_anyway", "label": "Ask anyway"}
    out = {"message_id": NEXT_ID[0], "intent": gate, "status": "answered", "layer": "national",
           "sentences": [], "sources": [], "local_rule_unknown": False, "where_to_ask": [],
           "actions": [], "disclaimer_key": "qa_disclaimer", "provider": "none",
           "cached": False, "degraded": False}
    if home:
        out["state_code"] = None
    fixed = {"greeting": "fixed_reply", "off_domain": "off_topic", "blocked": "blocked"}
    if gate in fixed:
        out["status"] = fixed[gate]
        out["actions"] = [] if gate == "blocked" else [ask_anyway]
        return out
    if gate == "new_problem":
        out |= {"status": "action", "actions": [
            {"kind": "start_report", "label": "Start a report"}, ask_anyway]}
        return out
    if gate == "my_complaint" and not REPORTS:  # B-073: nothing to pick
        out |= {"status": "action", "actions": [
            {"kind": "start_report", "label": "Start a report"}, ask_anyway]}
        return out
    if gate == "my_complaint":
        out |= {"status": "action", "actions": [
            {"kind": "pick_report", "label": f"{(r['category'] or 'Report').title()} · "
             f"{r['created_at'][:10]}", "report_id": r["report_id"]} for r in REPORTS[:5]]
            + [ask_anyway]}
        return out
    state = place(text, body) if home else "DL"
    last = next((m for m in reversed(CHAT) if m.get("state_code")), None) if home else None
    if home and state is None and last:  # memory: a follow-up keeps the chosen state
        state = last["state_code"]
    if home:
        out["state_code"] = state
        if state is None:
            out["status"] = "needs_location"
            return out
    if "#notfound" in text:
        out |= {"status": "not_found", "provider": "groq", "where_to_ask": [CPGRAMS]}
        return out
    low = text.lower()
    lines = next(a for keys, a in ANSWERS if not keys or any(k in low for k in keys))
    if not home and re.search(r"why|kyun|department", low):  # this report's facts first
        lines = [("Your report went to MCD because the road is narrower than 60 ft.",
                  ["Y1"])] + lines
    outside = state not in ("DL", "IN")
    if state != "DL":  # Delhi documents are filtered out anywhere else
        lines = [x for x in lines if "light" not in x[1]] or ANSWERS[-1][1]
    if state != "HR":  # the Haryana answer only for Haryana
        lines = [x for x in lines if "cmwindow" not in x[1] and len(x) < 3] or ANSWERS[-1][1]
    lines = [(*x, None) if len(x) == 2 else x for x in lines]
    responsible = any(x[2] == "responsible" for x in lines)
    ids = {}
    for _, keys, _ in lines:
        for k in keys:
            if k not in ids:
                ids[k] = "Y1" if k == "Y1" else f"C{sum(v[0] == 'C' for v in ids.values()) + 1}"
    out |= {"provider": "groq",
            "layer": {"DL": "delhi", "IN": "national"}.get(state, "state"),
            "sentences": [{"text": s, "cites": [ids[k] for k in keys],
                           **({"part": part} if part else {}),
                           **({"general_rule": True} if part == "responsible" and not keys
                              else {})}
                          for s, keys, part in lines],
            "sources": [{"id": i, **SRC[k]} for k, i in ids.items()],
            # a "who is responsible" line already says it, so no "I don't know the local rule"
            "local_rule_unknown": outside and not responsible}
    if outside:  # the state's own channel; CPGRAMS only when there is nothing else (B-065)
        out["where_to_ask"] = [PORTALS[state]] if state in PORTALS else [CPGRAMS]
    if not home:  # /ask: the dossier's channels; one department row per channel (B-072)
        name = DOSSIER["department"]["name_en"]
        out["where_to_ask"] = [
            {"label": name, "value": "155305", "deep_link": "tel:155305"},
            {"label": name, "value": "https://mcdonline.nic.in", "deep_link":
             "https://mcdonline.nic.in"},
            {"label": name, "value": "155305", "deep_link": "tel:155305"}]
    prev = next((m for m in reversed(CHAT) if m["role"] == "user"), None) if home else None
    own = OWN.search(text.lower()) or (prev and OWN.search(prev["text"].lower())
                                        and "this" in text.lower())
    if own:  # own problem + a question: the answer AND Start a report (FIX-PLAN F)
        town = next((n.title() for n, c in PLACES.items() if c == state and n in
                     (text + " " + (prev or {}).get("text", "")).lower()), "")
        prefill = re.sub(r"\s*#\w+", "", text if OWN.search(text.lower()) else prev["text"])
        out["actions"] = [{"kind": "start_report", "label": "Start a report",
                           "prefill": f"{prefill} ({town})" if town and town.lower() not in
                           prefill.lower() else prefill}]
    return out


def sse(done: dict, down: bool) -> bytes:
    """The answer as server-sent events: steps, each sentence with its new sources, done."""
    events = [("step", {"key": "qa_step_intent"})]
    if done["sentences"] or done["status"] == "not_found" or down:
        events += [("step", {"key": "qa_step_search"}),
                   ("step", {"key": "qa_step_found", "n": len(done["sources"]) or 3}),
                   ("step", {"key": "qa_step_check"})]
    if down:  # both AIs failed mid-way
        events.append(("error", {"status": 503, "message_key": "qa_unavailable"}))
    else:
        by_id, sent = {s["id"]: s for s in done["sources"]}, set()
        for s in done["sentences"]:
            new = [by_id[c] for c in s["cites"] if c not in sent]
            sent |= set(s["cites"])
            events.append(("sentence", {**s, "sources": new}))
        events.append(("done", done))
    return "".join(f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n"
                   for e, d in events).encode()


def question(request: httpx.Request, report_id: str | None) -> httpx.Response:
    body = json.loads(request.read())
    text = (body.get("message") if report_id is None else body.get("question")) or ""
    text = re.sub(r"\b\d{10}\b", "[phone]", text.strip())  # mask_pii, roughly
    if not text or len(text) > 300 or not body.get("client_msg_id"):
        return error(422, "validation", "invalid_question")
    if report_id is not None:
        if report_id not in CONV:
            return error(404, "not_found", "report_not_found")
        if not any(m.get("kind") in ("dossier", "out_of_coverage") for m in CONV[report_id]):
            return error(409, "wrong_step", "qa_needs_dossier")
    if "#429" in text:
        return error(429, "rate_limited", "rate_limited_questions")
    if "#throttle" in text:
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": {
            "code": "rate_limited", "message_key": "qa_throttled", "retry_after": 2}})
    if "#busy" in text:
        return error(429, "rate_limited", "qa_busy")
    if "#503" in text:
        return error(503, "provider_down", "qa_unavailable")
    down = "#down" in text
    done = SEEN.get(body["client_msg_id"])
    if done is None:
        done = qa(text, body, report_id)
        if not down:  # stored like chat_event / report_event
            SEEN[body["client_msg_id"]] = done
            shown = re.sub(r"\s*#\w+", "", text)
            said = {"role": "assistant", "message_id": done["message_id"],
                    "status": done["status"], "layer": done["layer"],
                    # stored text keeps the [C1] markers (§5)
                    "text": " ".join(s["text"] + "".join(f"[{c}]" for c in s["cites"])
                                     for s in done["sentences"]),
                    "message_key": done["message_key"], "state_code": done["state_code"],
                    "state_name": done["state_name"],
                    "sentences": done["sentences"], "sources": done["sources"],
                    "local_rule_unknown": done["local_rule_unknown"], "vote": None,
                    "options": done["where_to_ask"], "intent": done["intent"],
                    "actions": done["actions"], "at": AT}
            if report_id is None:
                CHAT.extend([{"role": "user", "kind": "chat", "text": shown, "at": AT},
                             {**said, "kind": "chat_answer"}])
            else:
                CONV[report_id].extend([{"role": "user", "kind": "ask", "text": shown, "at": AT},
                                        {**said, "kind": "ask_answer"}])
    if "text/event-stream" not in request.headers.get("Accept", ""):
        return httpx.Response(200, json=done)
    return httpx.Response(200, content=sse(done, down),
                          headers={"content-type": "text/event-stream"})


def feedback(request: httpx.Request, messages: list[dict], mid: str) -> httpx.Response:
    vote = json.loads(request.read()).get("vote")
    msg = next((m for m in messages if str(m.get("message_id")) == mid), None)
    if msg is None:
        return error(404, "not_found", "message_not_found")
    if vote not in ("up", "down"):
        return error(422, "validation", "invalid_input")
    msg["vote"] = vote  # the last vote wins
    return httpx.Response(204)


def handler(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if path == "/healthz":
        return httpx.Response(200, json={"ok": True})
    if method == "POST" and path == "/api/v1/auth/dev-login":
        name = json.loads(request.read())["name"]
        return httpx.Response(200, json={"token": TOKEN, "user": {"id": "u1", "name": name}})
    if method == "POST" and path == "/api/v1/auth/signup":  # email + password (supabase mode)
        body = json.loads(request.read())
        if body["email"] == "taken@example.com":
            return error(409, "email_taken", "email_taken")
        return httpx.Response(200, json={"token": TOKEN, "user": {"id": "u1",
                                                                  "name": body["name"]}})
    if method == "POST" and path == "/api/v1/auth/login":  # the password is "correct-horse"
        body = json.loads(request.read())
        if body["password"] != "correct-horse":
            return error(400, "bad_credentials", "bad_credentials")
        return httpx.Response(200, json={"token": TOKEN, "user": {"id": "u1", "name": "Priya"}})
    if request.headers.get("Authorization") != f"Bearer {TOKEN}":
        return error(401, "unauthorized", "login_required")
    if method == "POST" and path == "/api/v1/reports":
        return create_report(request.read())
    m = re.fullmatch(r"/api/v1/reports/([\w-]+)/(answer|conversation)", path)
    if m and method == "POST" and m.group(2) == "answer":
        return answer(m.group(1), json.loads(request.read()))
    if m and method == "GET" and m.group(2) == "conversation":
        if m.group(1) not in CONV:
            return error(404, "not_found", "report_not_found")
        return httpx.Response(200, content=json.dumps({"messages": CONV[m.group(1)]},
                                                      ensure_ascii=False).encode())
    if path == "/api/v1/chat":
        if method == "GET":
            return httpx.Response(200, content=json.dumps({"messages": CHAT[-50:]},
                                                          ensure_ascii=False).encode())
        return question(request, None)
    m = re.fullmatch(r"/api/v1/chat/(\d+)/feedback", path)
    if m and method == "POST":
        return feedback(request, CHAT, m.group(1))
    m = re.fullmatch(r"/api/v1/reports/([\w-]+)/ask", path)
    if m and method == "POST":
        return question(request, m.group(1))
    m = re.fullmatch(r"/api/v1/reports/([\w-]+)/ask/(\d+)/feedback", path)
    if m and method == "POST":
        return feedback(request, CONV.get(m.group(1), []), m.group(2))
    if method == "POST" and path == "/api/v1/geocode":
        text = json.loads(request.read())["text"]
        if "#502" in text:
            return error(502, "geocode_failed", "geocode_failed")
        return httpx.Response(200, json={"candidates": [] if "nothing" in text else CANDIDATES})
    if method == "GET" and path == "/api/v1/categories":
        return httpx.Response(200, json={"categories": CATEGORIES})
    if method == "GET" and path == "/api/v1/me":
        return httpx.Response(200, json={"user": {"id": "u1", "name": "Priya"}})
    if method == "GET" and path == "/api/v1/me/reports":
        return httpx.Response(200, json={"reports": REPORTS})
    if method == "GET" and path == f"/api/v1/dossiers/{DOSSIER_ID}":
        withheld = bool(LAST["photo"]) and "#withheld" in LAST["text"]
        d = {**DOSSIER, **LAST["decided"], "citizen_text": LAST["text"],
             "photo_withheld": withheld,
             "photo_url": MEDIA_URL if LAST["photo"] and not withheld else None}
        return httpx.Response(200, content=json.dumps(d, ensure_ascii=False).encode())
    if method == "GET" and path == MEDIA_URL and LAST["photo"] and "#withheld" not in LAST["text"]:
        return httpx.Response(200, content=LAST["photo"], headers={"content-type": "image/png"})
    return error(404, "not_found", "not_found")


transport = httpx.MockTransport(handler)
