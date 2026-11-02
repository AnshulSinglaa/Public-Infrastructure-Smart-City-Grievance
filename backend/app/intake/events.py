"""The chat transcript: every step of a report as a row in report_event.
Payloads hold MASKED text only, so the transcript can be shown as it is."""

from psycopg.types.json import Jsonb

from app.intake.catalog import categories, departments

# What the assistant says for each outcome (the UI may use its own words per `kind`).
SAY = {
    "rejected": "This photo doesn't show a civic problem. Please take another photo.",
    "out_of_coverage": "We don't cover this area yet. Here is where you can complain.",
}


def record(conn, report_id: str, role: str, kind: str, **payload) -> None:
    conn.execute("INSERT INTO report_event (report_id, role, kind, payload)"
                 " VALUES (%s, %s, %s, %s)", (report_id, role, kind, Jsonb(payload)))


def has(conn, report_id: str, kind: str) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM report_event WHERE report_id = %s"
                        " AND kind = %s)", (report_id, kind)).fetchone()[0]


def options_of(question: dict) -> list[dict]:
    """The choices a pending question offers, as [{value, label}] (+ why, confidence_label
    for pick_candidate cards)."""
    if question["kind"] == "issue_question":
        return question["options"]
    if question["kind"] == "pick_candidate":  # with each card's reason (B-046)
        return [{"value": c["department"]["code"], "label": c["department"]["name_en"],
                 "why": c["why"], "confidence_label": c["confidence_label"]}
                for c in question["candidates"]]
    return [{"value": o["category"], "label": categories()[o["category"]]["name_en"]}
            for o in question["options"]]  # clarify_category


def question_text(question: dict) -> str:
    return question.get("text") or "What kind of problem is this?"


def record_question(conn, report_id: str, question: dict) -> None:
    record(conn, report_id, "assistant", "question", text=question_text(question),
           options=options_of(question), question_kind=question["kind"],
           retry=bool(question.get("retry")))


def button(label: str, value: str, deep_link: str | None) -> dict:
    """A tappable choice in the chat: a call or a link (same fields as the API channels)."""
    return {"value": value, "label": label, "deep_link": deep_link}


def record_emergency(conn, report_id: str, card: dict) -> None:
    """Once per report, even when the graph is resumed several times. The call-first
    numbers are stored as options, so the call buttons still work after a refresh."""
    if not has(conn, report_id, "emergency"):
        numbers = ", ".join(f"{c['label']} {c['value']}" for c in card["call_first"])
        record(conn, report_id, "assistant", "emergency",
               text=f"This looks dangerous. Call first: {numbers}.",
               options=[button(c["label"], c["value"], c["deep_link"])
                        for c in card["call_first"]])


def record_rejected(conn, report_id: str, reason: str) -> None:
    record(conn, report_id, "assistant", "rejected", text=SAY["rejected"], reason=reason)


def record_out_of_coverage(conn, report_id: str, next_step: dict,
                           directory: dict | None) -> None:
    """The same channels the POST /reports response gave: Delhi helplines, or (outside
    Delhi) the state's portal and helpline if verified, plus CPGRAMS."""
    options = [button(c["label"], c["value"], c["deep_link"]) for c in next_step["channels"]]
    if directory:
        if directory["portal_url"]:
            options.append(button(directory["portal_name"], directory["portal_url"],
                                  directory["portal_url"]))
            if directory["helpline"]:
                h = directory["helpline"]
                options.append(button(f"Call {h}", h, f"tel:{h}"))
        national = directory["national"]
        options.append(button(national["name"], national["url"], national["url"]))
    record(conn, report_id, "assistant", "out_of_coverage", text=SAY["out_of_coverage"],
           options=options, outside_delhi=next_step["outside_delhi"])


def record_dossier(conn, report_id: str, dossier_id: str, department_code: str) -> None:
    name = departments().get(department_code, {}).get("name_en", department_code)
    record(conn, report_id, "assistant", "dossier", dossier_id=dossier_id,
           text=f"Your complaint for {name} is ready.")


def conversation(conn, report_id: str) -> list[dict]:
    rows = conn.execute("SELECT role, kind, payload, at FROM report_event"
                        " WHERE report_id = %s AND kind <> 'model_call'"  # limit counter only
                        " ORDER BY id", (report_id,)).fetchall()
    out = []
    for role, kind, payload, at in rows:
        msg = {"role": role, "kind": kind, "text": payload.get("text", ""), "at": at.isoformat()}
        for key in ("options", "dossier_id", "retry", "outside_delhi", "reason"):
            if key in payload:
                msg[key] = payload[key]
        out.append(msg)
    return out
