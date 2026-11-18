"""One Q&A turn, shared by POST /reports/{id}/ask and POST /chat (HLD §6, §6A, FIX-PLAN-S6).

    injection rules -> blocked (0 tokens)
    intent gate     -> greeting / off_domain / new_problem / my_complaint on /chat: fixed reply
                       or action (0 big-model tokens); /chat with no place -> needs_location
    case (slots: place, local body, issue, scope, own problem; app/rag/slots.py)
    retrieve (layers by place, applicability by scope) -> [C1..C4] (trimmed)
      + this report's facts [Y1..Yn] (written by code)
    gateway (Groq -> Gemini) -> a plan: responsible -> where -> steps -> escalation -> notes
    output guards -> verified sentences; wrong script -> one retry, then dropped
    code adds: "your local body (general rule)" when no document names who is responsible;
    where_to_ask from our DB (CPGRAMS only for central matters); start_report for own problems

turn() is a generator of (event, data): "step", "sentence" (verified only), then "done" with
the whole response, or "error" (busy / both providers down). The caller stores the answer
and streams the events (or returns only the "done" body as JSON).
"""

import re

from app.config import settings
from app.db import connect
from app.guards import input as guard_in
from app.guards import output as guard_out
from app.intake.catalog import categories, departments, states
from app.llm import gateway
from app.providers.common import ProviderDown, load_prompt
from app.rag import retrieve
from app.rag.slots import devanagari_ratio, language
from app.routing.engine import NATIONAL

RTI_ONLINE = {"label": "RTI Online", "value": "https://rtionline.gov.in",
              "deep_link": "https://rtionline.gov.in"}
FIXED = {  # status, message_key for the 0-token replies
    "blocked": ("blocked", "qa_blocked"),
    "greeting": ("fixed_reply", "chat_greeting"),
    "off_domain": ("off_topic", "qa_off_topic"),
    "new_problem": ("action", "chat_start_report"),
    "my_complaint": ("action", "chat_pick_report"),
    "needs_location": ("needs_location", "chat_which_state"),
}
MESSAGE_KEY = {"answered": None, "not_found": "qa_not_found", "out_of_scope": "qa_out_of_scope"}
FAKE_TAG = re.compile(r"</?\s*(doc|fact|question|history|case)\b[^>]*>", re.IGNORECASE)
CENTRAL_SCOPES = {"central_ministry_matters", "national_highways"}
REPLY_IN = {"en": "English", "hinglish": "Hinglish (Hindi words in Latin letters, no Devanagari)",
            "hi": "Hindi in Devanagari script"}
GENERAL_RULE = {  # the uncited "who is responsible" line, written by code (FIX-PLAN-S6 C)
    "en": "Usually your local body, {body}, handles this kind of problem (a general rule, "
          "not from an official document).",
    "hinglish": "Aam taur par aapki local body, {body}, yeh kaam dekhti hai (general rule, "
                "official document se nahi).",
    "hi": "आम तौर पर आपका स्थानीय निकाय, {body}, यह काम देखता है (सामान्य नियम, आधिकारिक "
          "दस्तावेज़ से नहीं)।",
}
ANY_BODY = "your municipal body (municipal corporation, council or committee)"


def channel(label: str, value: str, link: str | None) -> dict:
    return {"label": label, "value": value, "deep_link": link}


def where_to_ask(conn, *, delhi: bool, state_code: str | None, department: str | None,
                 scope: str) -> list[dict]:
    """From our DB and CSVs only, never from model text: the department's channels (one
    label per kind, B-072), then PGMS (Delhi) or the state's verified portal + helpline;
    CPGRAMS only for a central matter or a place without its own portal (B-065); RTI Online
    for RTI."""
    from app.api.reports import csv_channels  # the dossier's own fallback for channels

    out = []
    if department:
        name = departments().get(department, {}).get("name_en", department)
        rows = conn.execute(
            "SELECT c.channel_type, c.value, c.deep_link FROM contact c JOIN department d"
            " ON d.id = c.department_id WHERE d.code = %s ORDER BY c.id", (department,)).fetchall()
        rows = rows or [(c["type"], c["value"], c["deep_link"]) for c in csv_channels(department)]
        label = {"helpline": "Call {v}", "portal": f"{name} portal", "email": f"Email {name}"}
        out += [channel(label.get(kind, name).replace("{v}", value), value, link)
                for kind, value, link in rows]
    own_portal = False
    if delhi:
        out += [channel(c["label"], c["value"], c["deep_link"]) for c in csv_channels("PGMS")
                if c["type"] == "portal"]
        own_portal = True
    elif state_code and state_code in states():
        s = states()[state_code]
        if s.get("verified", "").lower() in ("yes", "true", "1") and s["portal_url"]:
            out.append(channel(s["portal_name"], s["portal_url"], s["portal_url"]))
            own_portal = True
            if s["helpline"]:
                out.append(channel(f"Call {s['helpline']}", s["helpline"], f"tel:{s['helpline']}"))
    if scope in CENTRAL_SCOPES or not own_portal:
        out.append(channel(NATIONAL["name"], NATIONAL["url"], NATIONAL["url"]))
    if scope == "rti":
        out.append(RTI_ONLINE)
    return out


def layer_of(delhi: bool, state_code: str | None) -> str:
    return "delhi" if delhi else "state" if state_code and state_code != "IN" else "national"


def clean(text) -> str:
    return FAKE_TAG.sub("", str(text or ""))


def case_block(ctx: dict, lang: str) -> str:
    """What we know, for the model: place + local body, issue, scope, reply language."""
    slots, lines = ctx.get("slots") or {}, []
    town = slots.get("town")
    where = f"{town['name']} ({town['label']}, {town['district']} district)" if town else None
    place = ", ".join(x for x in (where, ctx.get("state_name")) if x)
    if place:
        lines.append(f"place: {place}")
    if slots.get("issue_type"):
        lines.append(f"issue: {slots['issue_type'].replace('_', ' ')}"
                     + ("; it is the user's own problem" if slots.get("own_problem") else ""))
    scope = slots.get("scope", "local_body_matters")
    lines.append("level: " + ("local/state matter: central portals such as CPGRAMS don't apply"
                              if scope == "local_body_matters" else scope.replace("_", " ")))
    lines.append(f"reply language: {REPLY_IN[lang]}")
    return "<case>\n" + "\n".join(clean(x) for x in lines) + "\n</case>"


def prompt(ctx: dict, chunks: list[dict], lang: str) -> str:
    parts = [case_block(ctx, lang)]
    parts += [f'<fact id="{f["id"]}">{clean(f["quote"])}</fact>' for f in ctx.get("facts", [])]
    for i, c in enumerate(chunks, 1):
        attrs = " ".join(f'{k}="{clean(v).replace(chr(34), "")}"' for k, v in (
            ("title", c["title"]), ("issuer", c["issuer"]), ("level", c.get("level")),
            ("section", c["section_ref"])) if v)
        parts.append(f'<doc id="C{i}" {attrs}>\n{clean(c["excerpt"])}\n</doc>')
    if ctx["history"]:
        parts.append("<history>\n" + "\n".join(f"Q: {clean(q)[:200]}\nA: {clean(a)[:300]}"
                                                for q, a in ctx["history"]) + "\n</history>")
    parts.append(f"<question>\n{clean(ctx['question'])}\n</question>")
    return "\n\n".join(parts)


def doc_source(cid: str, c: dict, quotes: list[str]) -> dict:
    return {"id": cid, "kind": "document", "doc_title": c["title"], "issuer": c["issuer"],
            "doc_type": c["doc_type"], "tier": c["tier"], "layer": c["layer"],
            "level": c.get("level"),
            "doc_date": c["issued_on"].isoformat() if c["issued_on"] else None,
            "section_ref": c["section_ref"], "page": c["page"], "quote": quotes[0],
            "quotes": quotes, "url": c["url"],
            "fetched_at": c["fetched_at"].date().isoformat() if c["fetched_at"] else None,
            "stale": bool(c["stale"]), "link_status": "broken" if c["broken"] else "ok",
            "superseded_note": c.get("superseded_note")}


def base_body(ctx: dict) -> dict:
    return {"message_id": None, "intent": None, "status": None,
            "layer": layer_of(ctx.get("delhi", False), ctx.get("state_code")),
            "sentences": [], "sources": [], "local_rule_unknown": False, "where_to_ask": [],
            "responsible_body": None, "actions": [], "message_key": None,
            "disclaimer_key": "qa_disclaimer", "provider": "none", "cached": False,
            "degraded": False, "tokens": 0,
            "state_code": ctx.get("state_code"), "state_name": ctx.get("state_name")}


def start_report(ctx: dict) -> dict:
    """The report composer, prefilled with the masked text plus the town (FIX-PLAN-S6 F)."""
    town = ((ctx.get("slots") or {}).get("town") or {}).get("name")
    text = ctx["question"]
    prefill = text + (f" ({town})" if town and town.casefold() not in text.casefold() else "")
    return {"kind": "start_report", "label": "Start a report", "prefill": prefill}


def fixed(ctx: dict, body: dict, kind: str) -> dict:
    status, key = FIXED[kind]
    body |= {"status": status, "message_key": key, "layer": None}  # no documents used (P9)
    if kind == "new_problem":
        body["actions"] = [start_report(ctx)]
    elif kind == "my_complaint":
        body["actions"] = [{"kind": "pick_report", "label": r["label"], "report_id": r["id"]}
                           for r in ctx.get("my_reports", [])]
        if not body["actions"]:  # nothing to pick (B-073)
            body |= {"message_key": "chat_no_reports", "actions": [start_report(ctx)]}
    # needs_location has no actions: the UI shows its own state buttons and resends the
    # question with `state_code`.
    if kind in ("greeting", "off_domain", "new_problem", "my_complaint"):
        body["actions"].append({"kind": "ask_anyway", "label": "Ask anyway"})
    return body


def wrong_script(text: str, lang: str) -> bool:
    ratio = devanagari_ratio(text)
    return ratio < 0.3 if lang == "hi" else ratio > 0.2


def generate(ctx: dict, chunks: list[dict], texts: dict, lang: str, scope: str, body: dict):
    """The model's plan, checked. One retry when its script doesn't match the question's
    (B-062); sentences still in the wrong script are dropped. Raises ProviderDown / Busy."""
    version, system = load_prompt("qa_answer")
    user = prompt(ctx, chunks, lang)
    for attempt in range(2):
        result, info = gateway.complete(
            "qa_answer", guard_out.QAPlan, system, user, prompt_version=version,
            report_id=ctx["trace_seed"], trace_input=ctx["question"],
            timeout=settings.qa_timeout_s, default=guard_out.NOT_FOUND)
        body |= {"provider": info["provider"] or "none", "cached": info["provider"] is None,
                 "tokens": body["tokens"] + info["tokens"]}
        checked = guard_out.check(result, texts, ctx.get("allowed"), question=ctx["question"],
                                  central_ok=scope != "local_body_matters")
        if attempt or not any(wrong_script(s["text"], lang) for s in checked["sentences"]):
            break
        user += f"\n\nIMPORTANT: write every sentence in {REPLY_IN[lang]}."
    kept = [s for s in checked["sentences"] if not wrong_script(s["text"], lang)]
    checked["dropped"] += ["language"] * (len(checked["sentences"]) - len(kept))
    if not kept and checked["status"] == "answered":
        checked["status"] = "not_found"
    return checked | {"sentences": kept}


def turn(ctx: dict):
    """ctx: endpoint ('ask' | 'chat'), question (MASKED), force_intent, trace_seed, history,
    delhi, state_code, state_name (None when unknown), slots (app/rag/slots.extract),
    facts [{id, label, quote}], department, allowed (department codes, or None on /chat),
    my_reports (/chat)."""
    q, body = ctx["question"], base_body(ctx)
    slots = ctx.get("slots") or {}
    scope = slots.get("scope", "local_body_matters")
    yield "step", {"key": "qa_step_intent"}
    if guard_in.is_injection(q):
        yield "done", fixed(ctx, body | {"intent": None}, "blocked")
        return
    if ctx.get("force_intent") == "general_civic":
        label = "general_civic"
    else:
        label, _how = guard_in.intent(q, ctx["trace_seed"])
    body["intent"] = label
    if label in ("greeting", "off_domain", "new_problem") or (
            label == "my_complaint" and ctx["endpoint"] == "chat"):
        yield "done", fixed(ctx, body, label)
        return
    if ctx["endpoint"] == "chat" and ctx.get("state_code") is None and not ctx.get("delhi"):
        yield "done", fixed(ctx, body, "needs_location")
        return

    delhi, state = ctx.get("delhi", False), ctx.get("state_code")
    lang = language(q)
    yield "step", {"key": "qa_step_search"}
    # A follow-up keeps its place and issue: they go into the search too (B-065).
    town = (slots.get("town") or {}).get("name")
    query = " ".join(x for x in (q, (slots.get("issue_type") or "").replace("_", " "), town,
                                 ctx.get("state_name") if state != "IN" else None) if x)
    with connect() as conn:
        found = retrieve.search(conn, query, delhi=delhi,
                                state_code=None if state == "IN" else state,
                                department=ctx.get("department"), scope=scope)
        body["where_to_ask"] = where_to_ask(conn, delhi=delhi, state_code=state,
                                            department=ctx.get("department"), scope=scope)
    chunks = found["chunks"]
    body["degraded"] = found["degraded"] or (settings.rerank and len(chunks) > 1
                                             and not found["reranked"]
                                             and not retrieve.DEVANAGARI.search(q))
    yield "step", {"key": "qa_step_found", "n": len(chunks)}
    facts = ctx.get("facts", [])
    texts = {f["id"]: f["quote"] for f in facts} | {f"C{i}": c["text"]
                                                    for i, c in enumerate(chunks, 1)}
    checked = {"status": "not_found", "sentences": [], "dropped": []}
    if chunks or facts:
        try:
            checked = generate(ctx, chunks, texts, lang, scope, body)
        except gateway.Busy as e:
            yield "error", {"status": 429, "message_key": "qa_throttled",
                            "retry_after": e.retry_in}
            return
        except ProviderDown:
            yield "error", {"status": 503, "message_key": "qa_unavailable"}
            return
    yield "step", {"key": "qa_step_check"}

    sentences = checked["sentences"]
    responsible = any(s["part"] == "responsible" for s in sentences)
    outside_delhi = bool(state) and state not in ("DL", "IN") and not delhi
    if (scope == "local_body_matters" and outside_delhi and not responsible
            and checked["status"] != "out_of_scope"):
        town_info = slots.get("town")
        name = town_info["label"] if town_info else ANY_BODY
        body["responsible_body"] = (town_info or {}) | {"name": name, "general_rule": True}
        sentences = [{"text": GENERAL_RULE[lang].format(body=name), "part": "responsible",
                      "cites": [], "general_rule": True}, *sentences]
        checked["status"], responsible = "answered", True
    by_id = {f["id"]: f for f in facts}
    quotes: dict[str, list[str]] = {}  # every verified quote per source (B-060)
    for s in sentences:
        for cid, quote in s["cites"]:
            if quote not in quotes.setdefault(cid, []):
                quotes[cid].append(quote)
    shown: dict[str, dict] = {}
    for s in sentences:
        new = []
        for cid, _ in s["cites"]:
            if cid in shown:
                continue
            if cid in by_id:
                shown[cid] = {"id": cid, "kind": "your_report", "label": by_id[cid]["label"],
                              "quote": quotes[cid][0], "quotes": quotes[cid]}
            else:
                shown[cid] = doc_source(cid, chunks[int(cid[1:]) - 1], quotes[cid])
            new.append(shown[cid])
        sentence = {"text": s["text"], "part": s["part"],
                    "cites": list(dict.fromkeys(c for c, _ in s["cites"])),
                    "quotes": [{"id": c, "quote": qt} for c, qt in s["cites"]]}
        if s.get("general_rule"):
            sentence["general_rule"] = True
        body["sentences"].append(sentence)
        yield "sentence", sentence | {"sources": new}
    body |= {"status": checked["status"], "sources": list(shown.values()),
             "message_key": MESSAGE_KEY[checked["status"]], "dropped": checked["dropped"],
             # "I don't know the local rule for {state}" (the UI writes it) only when we
             # couldn't even say who is responsible (HLD §5.2 #8)
             "local_rule_unknown": outside_delhi and not responsible}
    if ctx["endpoint"] == "chat" and slots.get("own_problem"):  # answer + a report (B-066)
        body["actions"] = [start_report(ctx)]
    yield "done", body


def answer_text(body: dict) -> str:
    """What the transcript shows: the sentences with their [C1] markers, or ''."""
    return " ".join(s["text"] + "".join(f"[{c}]" for c in s["cites"]) for s in body["sentences"])


# ---------- report facts for /ask (written by code, never by a model) ----------

def report_facts(conn, report_id) -> tuple[list[dict], str | None, set[str], str | None]:
    """([Y1..Yn], the report's department code, codes a sentence may name, category)."""
    row = conn.execute(
        "SELECT r.category, r.state, d.routing, d.deadline, d.pin, dep.code"
        " FROM report r LEFT JOIN dossier d ON d.report_id = r.id"
        " LEFT JOIN master_issue m ON m.id = r.master_id"
        " LEFT JOIN department dep ON dep.id = m.department_id WHERE r.id = %s",
        (report_id,)).fetchone()
    category, state, routing, deadline, pin, code = row
    cat = categories().get(category or "", {}).get("name_en", category or "unknown")
    lines = [("Your report", f"Your report is about: {cat}.")]
    allowed: set[str] = set()
    if state == "dossier_ready" and code:
        name = departments().get(code, {}).get("name_en", code)
        allowed = {code}
        lines.append(("Department", f"Our routing rules sent your report to {name} ({code})."))
        if routing and routing.get("why"):
            lines.append(("Routing reason", routing["why"]))
        for c in (routing or {}).get("candidates") or []:
            other = c.get("department", {}).get("code")
            if other and other != code:
                allowed.add(other)
                who = c["department"].get("name_en", other)
                lines.append(("Other possible department",
                              f"{who} ({other}) may also be responsible: {c.get('why', '')}"))
        if deadline and deadline.get("label") == "immediate":
            text = "This looks like an emergency: the letter asks for immediate action."
            lines.append(("Deadline", text))
        elif deadline and deadline.get("sla_days"):
            text = (f"Suggested deadline: {deadline['sla_days']} days."
                    " No official deadline was found for this rule.")
            lines.append(("Deadline", text))
        if pin and pin.get("ward_no"):
            ward = f"{pin['ward_no']} {pin.get('ward_name') or ''}".strip()
            lines.append(("Ward", f"The pin is in ward {ward}."))
    else:
        text = "We don't route reports in this area yet, so no department was chosen for it."
        lines.append(("Coverage", text))
    facts = [{"id": f"Y{i}", "label": label, "quote": text}
             for i, (label, text) in enumerate(lines, 1)]
    return facts, code if allowed else None, allowed, category
