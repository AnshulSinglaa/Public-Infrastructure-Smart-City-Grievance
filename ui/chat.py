"""The report as a chat (Sprint 5): the user's report, then every agent step as a bubble.

The whole thread is rebuilt from GET /reports/{id}/conversation on every run, so a refresh,
"My reports" and "Recent" all just set `st.session_state.thread` (the report id).

One rule for the options on a message:
- an option with a `value` is a quick reply (POST /answer {"value"}),
- an option with a `url` or `deep_link` is a link (call 112, open the state portal, …).
"""

from html import escape

import streamlit as st

from lib.api import ApiError, answer, conversation, get_dossier
from report import (
    NOTICE_PILLS,
    call_button,
    html,
    open_dossier,
    pill,
    show_error,
    start_new_report,
)
from strings import TEXT, t

# None = Streamlit's own user icon, which also lets style.css spot the user's bubbles.
AVATARS = {"user": None, "assistant": ":material/support_agent:"}
LETTER_PREVIEW = 170  # characters of the letter shown in the bubble


def state_directory() -> dict | None:
    """The state's name for an outside-Delhi thread sent in this session (the transcript
    doesn't store it); everything else comes from the transcript."""
    sent = st.session_state.get("sent") or {}
    if sent.get("thread") != st.session_state.get("thread"):
        return None
    return (sent.get("envelope") or {}).get("state_directory")


def say(text: str) -> str:
    """Assistant text may be a strings.py key ("retake_selfie") or plain text from the API."""
    return t(text) if text in TEXT else text


def send_answer(token: str, report_id: str, body: dict) -> None:
    # The spinner and any error go in a bubble at the bottom, where the user tapped or
    # typed, not at the top of a long thread (off-screen on a phone, B-054).
    with st.chat_message("assistant", avatar=AVATARS["assistant"]):
        try:
            with st.spinner(t("checking")):
                answer(token, report_id, body)
        except ApiError as e:  # rate_limited_answers, wrong_step, invalid_answer, …
            show_error(e, st.empty())
            return
    st.session_state.pop("reports", None)  # Recent shows the new state
    st.rerun()


def retake_photo() -> None:
    """Back to the composer with the text kept and the camera open. Streamlit forgets a
    widget's value while it isn't drawn, so the text comes back from what was sent."""
    sent = st.session_state.get("sent") or {}
    st.session_state.report_text = sent.get("text", "")
    st.session_state.pop("thread", None)
    st.session_state.pop("photo_draft", None)
    st.session_state.attach = "camera"


def links(options: list[dict], key: str) -> None:
    with st.container(horizontal=True, key=key):
        for i, o in enumerate(options):
            target = o.get("deep_link") or o.get("url")
            if target:
                call = target.startswith("tel:")
                # A call shows its number first, so a narrow phone can't cut it off (B-047).
                show_number = call and o.get("value") and o["value"] not in o["label"]
                label = f'{o["value"]} · {o["label"]}' if show_number else o["label"]
                st.link_button(label, target, type="primary" if i == 0 else "secondary",
                               icon=":material/call:" if call else ":material/open_in_new:")


def user_bubble(m: dict, first: bool) -> None:
    if m.get("text"):
        html(f'<p class="chat-text">{escape(m["text"])}</p>')
    sent = st.session_state.get("sent") or {}
    if first and sent.get("thread") == st.session_state.get("thread"):
        if sent.get("photo"):  # only in this session: the API never sends photos back here
            st.image(sent["photo"], width=120)
        if sent.get("chip"):
            html(f'<span class="pill muted-pill">📍 {escape(sent["chip"])}</span>')


def question_bubble(token: str, rid: str, m: dict, active: bool) -> None:
    text = say(m.get("text", ""))
    if m.get("retry"):  # free text that couldn't be matched: ask again, gently
        text = f"{t('answer_unclear')} {text}"
    html(f'<p class="chat-text">{escape(text, quote=False)}</p>')
    for o in m.get("options", []):  # pick_candidate cards: why + how sure, per department
        if o.get("why"):
            conf = t("conf_" + o.get("confidence_label", "unsure"))
            html(f'<div class="card chat-card pick-card"><b>{escape(o["label"])}</b> '
                 f'<span class="pill accent">{escape(conf)}</span>'
                 f'<p class="why">{escape(o["why"])}</p></div>')
    if not active:  # an old question: the answer is the next bubble
        return
    with st.container(horizontal=True, key="replies"):
        for o in m.get("options", []):
            if st.button(o["label"], key=f"reply_{o['value']}"):
                send_answer(token, rid, {"value": o["value"]})


def dossier_bubble(token: str, m: dict, i: int) -> None:
    """Compact card: department, ward, the "why" line, evidence, a letter preview, Call."""
    cache = st.session_state.setdefault("dossiers", {})
    if m["dossier_id"] not in cache:
        cache[m["dossier_id"]] = get_dossier(token, m["dossier_id"])
    d = cache[m["dossier_id"]]
    dept, pin, routing = d["department"], d.get("pin") or {}, d["routing"]
    ward = t("ward", n=pin["ward_no"], name=pin.get("ward_name", "")) if pin.get("ward_no") else ""
    pills = [pill(t("conf_" + routing.get("confidence_label", "unsure")), "accent")]
    if routing.get("curated"):
        pills.append(pill(t("curated"), "accent"))
    pills += [pill(evidence_label(e)) for e in routing.get("evidence", [])]
    why = routing.get("why") or d.get("why")
    letter = d["complaint"]["en"]
    preview = (letter if len(letter) <= LETTER_PREVIEW
               else letter[:LETTER_PREVIEW].rsplit(" ", 1)[0] + "…")  # cut at a word
    html(f'<p class="chat-text">{escape(say(m.get("text") or "dossier_title"))}</p>'
         f'<div class="card chat-card"><p class="label muted">{t("who_fixes")}</p>'
         f'<h3 class="dept">{escape(dept["name_en"])} '
         f'<span class="muted">· {escape(dept["code"])}</span></h3>'
         f'<p class="muted">{escape(ward)}</p>'
         + (f'<p class="why"><b>{t("why_label")}:</b> {escape(why)}</p>' if why else "")
         + f'<div class="pills">{"".join(pills)}</div>'
         f'<p class="letter-preview">{escape(preview)}</p></div>')
    with st.container(horizontal=True, key=f"dossier_actions_{i}"):
        channels = d.get("channels") or []
        if channels:  # the first helpline is the one big action
            call_button(channels[0], primary=True)
        if st.button(t("open_letter"), key=f"open_letter_{i}", icon=":material/description:"):
            sent = st.session_state.get("sent") or {}
            mine = sent.get("thread") == st.session_state.get("thread")
            open_dossier(token, m["dossier_id"], sent.get("notices", []) if mine else [])
            st.rerun()
        st.button(t("new_report"), key=f"chat_new_{i}", type="tertiary", icon=":material/add:",
                  on_click=start_new_report)
    sent = st.session_state.get("sent") or {}
    if sent.get("thread") == st.session_state.get("thread"):
        notes = [pill(t(n), "muted-pill") for n in sent.get("notices", []) if n in NOTICE_PILLS]
        if notes:
            html(f'<div class="pills">{"".join(notes)}</div>')
    if routing.get("fake"):
        html(f'<div class="notice">{t("demo_ai")}</div>')


def evidence_label(e: str) -> str:
    """"pwd_list" -> "On the PWD road list"; "zone:jj_cluster" -> "Zone: jj cluster"."""
    if e.startswith("zone:"):
        return t("ev_zone", zone=e[5:].replace("_", " "))
    return t(f"ev_{e}") if f"ev_{e}" in TEXT else e.replace("_", " ")


def render_emergency(m: dict) -> None:
    """Red card at the very top. It never blocks: the chat continues below."""
    with st.container(border=True, key="card_emergency"):
        st.markdown(f"#### ⚠️ {t('emergency_call_first')}")
        st.caption(t("emergency_help"))
        links(m.get("options", []), "emergency_calls")


def assistant_bubble(token: str, rid: str, m: dict, active: bool, i: int) -> None:
    kind = m.get("kind")
    if kind == "question":
        question_bubble(token, rid, m, active)
    elif kind == "dossier":
        dossier_bubble(token, m, i)
    elif kind == "rejected":  # junk photo: say why, offer a retake (the text is kept)
        reason = m.get("reason")
        text = t(f"retake_{reason}") if f"retake_{reason}" in TEXT else say(m["text"])
        html(f'<p class="chat-text">{escape(text)} {t("retake_keep_text")}</p>')
        if active:
            st.button(t("retake"), key="retake_btn", type="primary",
                      icon=":material/photo_camera:", on_click=retake_photo)
    elif kind == "out_of_coverage":
        outside, sd = m.get("outside_delhi"), state_directory()  # B-045: from the transcript
        if outside and sd:
            help_text = (t("state_help", state=sd["state"]) if sd.get("verified")
                         else t("state_unknown_help"))
        else:  # reopened: the state's name isn't in the transcript, its portal button is
            help_text = t("outside_help" if outside else "uncovered_help")
        title = t("outside_title" if outside else "uncovered_title")
        html(f'<p class="chat-text"><b>{title}</b><br>{escape(help_text)}</p>')
        links(m.get("options", []), f"links_{i}")
        if active:
            st.button(t("new_report"), key="chat_new_ooc", type="tertiary",
                      icon=":material/add:", on_click=start_new_report)
    else:
        html(f'<p class="chat-text">{escape(say(m.get("text", "")))}</p>')


def render_thread(token: str, rid: str) -> None:
    error = st.empty()
    try:
        messages = conversation(token, rid)
    except ApiError as e:
        show_error(e, error)
        return
    for m in messages:  # emergencies first, above everything
        if m.get("kind") == "emergency":
            render_emergency(m)
    shown = [m for m in messages if m.get("kind") != "emergency"]
    # Once there is a dossier, nothing is waiting for an answer: no live buttons (B-039).
    done = any(m.get("kind") == "dossier" for m in shown)
    for i, m in enumerate(shown):
        last = i == len(shown) - 1 and not (done and m.get("kind") == "question")
        with st.chat_message(m["role"], avatar=AVATARS.get(m["role"])):
            if m["role"] == "user":
                user_bubble(m, first=i == 0)
                continue
            try:
                assistant_bubble(token, rid, m, last, i)
            except ApiError as e:  # e.g. the dossier didn't load: say so in its bubble
                show_error(e, st.empty())
    if shown and shown[-1].get("kind") == "question" and not done:  # free text, any language
        typed = st.chat_input(t("type_answer"), key="answer_text", max_chars=300)
        if typed and typed.strip():
            send_answer(token, rid, {"text": typed.strip()})
