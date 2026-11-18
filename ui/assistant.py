"""Questions answered from official documents (Sprint 6, docs/contracts/sprint6-api.md §4, §4A).

Two places, one renderer:
- the home-page civic assistant (POST /chat, history from GET /chat), under the composer;
- "Ask about this complaint" at the end of a report thread (POST /reports/{id}/ask, history
  from GET /reports/{id}/conversation as kinds ask / ask_answer).

Both stream the same events: step lines, then each verified sentence, then `done`. While it
streams the bubble shows them live; afterwards the page reruns and the message is drawn from
the history like every other one. A button (example chip, Ask anyway, a state) only queues the
question in `qa_pending`; it is sent at the bottom of the page, where the new bubble belongs.
"""

import csv
import re
import time
import uuid
from html import escape
from pathlib import Path

import streamlit as st

from lib.api import (
    ApiError,
    ask_feedback,
    ask_stream,
    chat_feedback,
    chat_history,
    chat_stream,
)
from report import html, show_error, start_new_report
from strings import CHAT_EXAMPLES, TEXT, t

AVATAR = ":material/support_agent:"
STATES_CSV = Path(__file__).resolve().parents[1] / "data" / "state_directory.csv"
# The text for every answer that isn't made of sentences (0-token fixed replies and refusals).
FIXED = {"off_topic": "qa_off_topic", "blocked": "qa_blocked", "fixed_reply": "chat_greeting",
         "needs_location": "chat_which_state", "not_found": "qa_not_found",
         "out_of_scope": "qa_out_of_scope"}
MARKS = re.compile(r"((?:\s*\[[CY]\d+\])+)")  # "[Y3][C1]" in a stored answer's text
VOTES = {1: "up", 0: "down"}  # st.feedback("thumbs"): 1 = 👍, 0 = 👎
# 👍/👎 under real answers and refusals (a wrong "outside civic complaints" needs a 👎 too),
# not under greetings, buttons-only replies or the state question.
RATED = ("answered", "not_found", "out_of_scope", "off_topic")
# A structured answer (FIX-PLAN-S6 A): each sentence's `part`, in the order it is shown.
# The "Where to complain" buttons go right after the HEAD parts.
HEAD, TAIL = ("responsible", "where"), ("step", "escalation", "note", None)
PART_LABEL = {"responsible": "qa_responsible", "where": "qa_where", "step": "qa_steps",
              "escalation": "qa_escalation"}
MAX_WAIT_S = 60  # a longer retry_after is shown, but the countdown stops here


@st.cache_data
def state_names() -> dict[str, str]:
    """{"RJ": "Rajasthan", …} from data/state_directory.csv; {} if the file isn't there."""
    try:
        with STATES_CSV.open(encoding="utf-8") as f:
            return {r["state_code"]: r["state_name"] for r in csv.DictReader(f)}
    except OSError:
        return {}


# ---------- one answer ----------

def cite_ids(s: dict) -> list[str]:
    # The response sends ids ("C1"); accept {"id", "quote"} too, as the model writes them.
    return [c if isinstance(c, str) else c.get("id", "") for c in s.get("cites", [])]


def numbered(sentences: list[dict], sources: list[dict]) -> list[tuple[int, dict]]:
    """Sources numbered 1, 2, … in the order the sentences first cite them."""
    by_id, order = {s["id"]: s for s in sources}, []
    for s in sentences:
        order += [c for c in cite_ids(s) if c in by_id and c not in order]
    return [(i + 1, by_id[c]) for i, c in enumerate(order)]


def plain(text: str) -> str:
    """Escaped for HTML body text (apostrophes kept as they are)."""
    return escape(text, quote=False)


def chip(n: int, src: dict) -> str:
    """A small numbered source chip: links to the official document, the quote on hover."""
    quote = escape(src.get("quote") or "")
    url = src.get("url") or ""
    if url.startswith(("https://", "http://")):
        return (f'<a class="cite" href="{escape(url)}" target="_blank" rel="noopener" '
                f'title="{quote}" aria-label="{t("sources")} {n}">{n}</a>')
    return f'<span class="cite" title="{quote}">{n}</span>'


def from_text(text: str) -> list[dict]:
    """A stored answer's `text` ("… [Y3][C1] …", §5) as sentences with cites, so its
    markers become the same numbered chips, never raw "[C1]"."""
    bits = MARKS.split(text)  # text, markers, text, markers, …
    marks = bits[1::2] + [""]
    return [{"text": s, "cites": re.findall(r"[CY]\d+", marks[i])}
            for i, s in enumerate(bits[::2]) if s.strip()]


def structured(sentences: list[dict]) -> bool:
    return any(s.get("part") for s in sentences)


def answer_html(sentences: list[dict], sources: list[dict], only=None) -> str:
    """Model text is shown as plain text (escaped), each sentence followed by its chips.
    A structured answer is grouped: Who is responsible (a "General rule" tag when it has no
    citation) → Where to complain → numbered Steps → If nothing happens. `only` = the parts
    to draw now (the chip numbers still count every sentence)."""
    nums = {src["id"]: n for n, src in numbered(sentences, sources)}
    by_id = {s["id"]: s for s in sources}

    def line(s: dict) -> str:
        cites = [c for c in cite_ids(s) if c in nums]
        out = plain(MARKS.sub("", s["text"]).strip())
        out += "".join(chip(nums[c], by_id[c]) for c in cites)
        if s.get("part") == "responsible" and (s.get("general_rule") or not cites):
            out += f' <span class="pill muted-pill rule-tag">{t("qa_general_rule")}</span>'
        return out

    if not structured(sentences):
        return f'<p class="chat-text answer">{" ".join(line(s) for s in sentences)}</p>'
    out = []
    for part in (*HEAD, *TAIL):
        if only is not None and part not in only:
            continue
        group = [s for s in sentences if s.get("part") == part
                 or (part is None and s.get("part") not in PART_LABEL)]
        if not group:
            continue
        label = (f'<p class="qa-label">{t(PART_LABEL[part])}</p>'
                 if part in PART_LABEL else "")
        if part == "step":
            items = "".join(f"<li>{line(s)}</li>" for s in group)
            out.append(f'{label}<ol class="qa-steps">{items}</ol>')
        else:
            out.append(f'{label}<p class="chat-text answer">{" ".join(line(s) for s in group)}'
                       "</p>")
    return f'<div class="qa-answer">{"".join(out)}</div>' if out else ""


def source_line(n: int, src: dict) -> str:
    if src.get("kind") == "your_report":
        head = f'{t("source_your_report")} · {src.get("label", "")}'
        meta = []
    else:
        where = " · ".join(str(x) for x in (src.get("section_ref"),
                                            f'p. {src["page"]}' if src.get("page") else None)
                           if x)
        head = " · ".join(x for x in (src.get("doc_title"), src.get("issuer"), where) if x)
        meta = [src["doc_date"] if src.get("doc_date") else t("citation_date_unknown")]
        if src.get("tier") == 2:
            parl = src.get("doc_type") == "parliament_answer"
            meta.append(t("citation_tier_parliament" if parl else "citation_tier_govt_news"))
        if src.get("stale"):
            meta.append(t("citation_stale", date=src.get("fetched_at", "")))
        if src.get("link_status") == "broken":
            meta.append(t("citation_link_broken", date=src.get("fetched_at", "")))
    link = ""
    if (src.get("url") or "").startswith("http"):
        link = (f' <a href="{escape(src["url"])}" target="_blank" rel="noopener">'
                f'{t("open_source")}</a>')
    return (f'<div class="source"><span class="cite">{n}</span> <b>{plain(head)}</b>'
            + (f'<span class="muted"> · {plain(" · ".join(meta))}</span>' if meta else "")
            + link
            + (f'<blockquote>{plain(src["quote"])}</blockquote>' if src.get("quote") else "")
            + "</div>")


def no_reports(m: dict) -> bool:
    """B-073: a "which complaint?" reply with no complaint to pick."""
    picks = any(a.get("kind") == "pick_report" for a in m.get("actions") or [])
    asked = m.get("intent") == "my_complaint" or m.get("message_key") in (
        "chat_pick_report", "chat_no_reports")
    return m.get("status") == "action" and asked and not picks


def reply_key(m: dict) -> str | None:
    if no_reports(m):
        return "chat_no_reports"
    if m.get("message_key") in TEXT:  # the API names it (6b); older rows: from the status
        return m["message_key"]
    if m.get("status") == "action":
        picks = any(a.get("kind") == "pick_report" for a in m.get("actions") or [])
        return "chat_pick_report" if picks else "chat_start_report"
    return FIXED.get(m.get("status"))


def distinct(options: list[dict]) -> list[dict]:
    """B-072: one button per target, and no two buttons with the same words. Rows that share
    a label (the department name) say what they are: Call 1800…, Email, Website."""
    labels = [o.get("label", "") for o in options]
    seen, out = set(), []
    for o in options:
        target = o.get("deep_link") or o.get("url")
        if not target or target in seen:
            continue
        seen.add(target)
        label = o.get("label") or o.get("value") or target
        if labels.count(label) > 1:
            if target.startswith("tel:"):
                label = t("call", n=o.get("value") or target[4:])
            else:
                kind = "qa_email" if target.startswith("mailto:") else "qa_website"
                label = f"{label} · {t(kind)}"
        out.append({**o, "label": label, "target": target})
    return out


def links(options: list[dict], key: str) -> None:
    """where_to_ask: department channels, the state portal, PGMS / CPGRAMS, RTI Online."""
    with st.container(horizontal=True, key=key):
        for o in distinct(options):
            call = o["target"].startswith("tel:")
            st.link_button(o["label"], o["target"],
                           icon=":material/call:" if call else ":material/open_in_new:")


def render_answer(token: str, where: str, m: dict, question: str, active: bool) -> None:
    """One assistant answer from the history. `where` = "chat" or the report id;
    `question` = the user's message before it (for Ask anyway / a state choice)."""
    mid = m.get("message_id")
    sources = m.get("sources") or []
    sentences = m.get("sentences") or []
    options = m.get("options") or m.get("where_to_ask") or []
    if m.get("status") == "answered":
        sentences = sentences or from_text(m.get("text", ""))
        if structured(sentences):  # who → where (+ its buttons) → steps → escalation
            html(answer_html(sentences, sources, only=HEAD))
            if options:
                links(options, f"where_{where}_{mid}")
                options = []
            html(answer_html(sentences, sources, only=TAIL))
        else:
            html(answer_html(sentences, sources))
    else:
        key = reply_key(m)
        text = t(key) if key else m.get("text", "")
        html(f'<p class="chat-text">{plain(text)}</p>')
    if m.get("local_rule_unknown"):
        code = m.get("state_code") or ""
        state = m.get("state_name") or state_names().get(code, code)
        html(f'<p class="chat-text">{plain(t("qa_local_rule_unknown", state=state))}</p>')
    nums = numbered(sentences, sources)
    if nums:
        with st.expander(f'{t("sources")} ({len(nums)})', key=f"src_{where}_{mid}"):
            html("".join(source_line(n, s) for n, s in nums))
    if options:
        links(options, f"where_{where}_{mid}")
    if active:
        actions(where, m, question)
    if m.get("status") == "answered":
        st.caption(t(m.get("disclaimer_key") or "qa_disclaimer"))
    if mid is not None and m.get("status") in RATED:
        vote = {"up": 1, "down": 0}.get(m.get("vote"))
        st.feedback("thumbs", key=f"fb_{where}_{mid}", default=vote,
                    on_change=send_vote, args=(token, where, mid))


def actions(where: str, m: dict, question: str) -> None:
    """The buttons of the latest answer: Start a report, the reports to pick, Ask anyway,
    and the state picker for a needs_location reply."""
    acts = m.get("actions") or []
    none = no_reports(m)
    if none and not any(a.get("kind") == "start_report" for a in acts):
        acts = [{"kind": "start_report"}, *acts]
    with st.container(horizontal=True, key=f"acts_{where}"):
        for i, a in enumerate(acts):
            kind = a.get("kind")
            if kind == "start_report":  # also next to an answer (own problem, FIX-PLAN F)
                prefill = "" if none else a.get("prefill") or question
                st.button(t("start_report"), key=f"act_start_{where}",
                          type="primary", icon=":material/add_a_photo:",
                          on_click=start_report, args=(where, prefill))
            elif kind == "pick_report" and a.get("report_id"):
                st.button(a.get("label") or a["report_id"], key=f"act_pick_{i}",
                          on_click=open_report, args=(a["report_id"],))
            elif kind == "ask_anyway":
                st.button(t("chat_ask_anyway"), key=f"act_anyway_{where}", type="tertiary",
                          on_click=queue, args=(where, question),
                          kwargs={"force_intent": "general_civic"})
        if m.get("status") == "needs_location":
            for code, label in (("DL", t("state_delhi")), ("IN", t("state_all_india"))):
                st.button(label, key=f"state_{code}", on_click=queue, args=(where, question),
                          kwargs={"state_code": code})
    if m.get("status") == "needs_location":
        others = {c: n for c, n in state_names().items() if c != "DL"}
        st.selectbox(t("state_other"), list(others), index=None, key="state_pick",
                     format_func=others.get, placeholder=t("state_other"),
                     label_visibility="collapsed",
                     on_change=lambda: queue(where, question,
                                             state_code=st.session_state.state_pick))


# ---------- button callbacks ----------

def queue(where: str, text: str, **extra) -> None:
    """The client_msg_id is made here and kept until `done` arrives, so a rerun or an
    interrupted stream resends the same id and the server replays the answer (F-01)."""
    st.session_state.qa_pending = {"where": where, "text": text, "id": str(uuid.uuid4()),
                                   **extra}


def retry(pending: dict) -> None:
    st.session_state.qa_pending = pending


def start_report(where: str, text: str) -> None:
    """new_problem: open the photo + location composer with the text already in it."""
    if where != "chat":  # from a report thread: a fresh composer
        start_new_report()
    st.session_state.report_text = text
    st.session_state.view = "new"
    st.session_state.prefilled = True


def open_report(report_id: str) -> None:
    st.session_state.thread = report_id
    st.session_state.view = "new"


def send_vote(token: str, where: str, mid: int) -> None:
    vote = VOTES.get(st.session_state.get(f"fb_{where}_{mid}"))
    if not vote:  # the vote was taken back: nothing to send
        return
    try:
        if where == "chat":
            chat_feedback(token, mid, vote)
        else:
            ask_feedback(token, where, mid, vote)
    except ApiError as e:
        st.toast(t(e.message_key))
        return
    st.toast(t("qa_feedback_thanks"))


# ---------- sending, with the live stream ----------

def step_text(data: dict) -> str:
    key, extra = data.get("key", ""), {k: v for k, v in data.items() if k != "key"}
    if key not in TEXT:
        return t("qa_checking")
    try:
        return t(key, **extra)
    except (KeyError, IndexError):  # a step with a number we don't know how to show
        return t(key)


def show_stream(events, box) -> dict | None:
    """Step lines live in a status box (inside `box`, so an error can clear it), then each
    verified sentence as it arrives. Returns the `done` object (None if there was none)."""
    status = box.status(t("qa_step_intent"), expanded=True)
    text_slot = st.empty()
    sentences, sources, done = [], [], None
    for event, data in events:
        if event == "step":
            line = step_text(data)
            status.update(label=line)
            status.caption(line)
        elif event == "sentence":
            sentences.append(data)
            sources += data.get("sources") or []
            text_slot.markdown(answer_html(sentences, sources), unsafe_allow_html=True)
        elif event == "done":
            done = data
    status.update(label=t("qa_step_done"), state="complete", expanded=False)
    return done


def countdown(e: ApiError, pending: dict) -> None:
    """Busy / throttled with a retry_after: "try again in N s", counting down, then a
    Try again button that resends the same question (same client_msg_id)."""
    slot = st.empty()
    wait = int(min(e.retry_after or 0, MAX_WAIT_S) + 0.999)
    for n in range(wait, 0, -1):  # ponytail: blocks this run; any click interrupts it
        slot.warning(f"{t(e.message_key)} {t('qa_retry_in', n=n)}")
        time.sleep(1)
    slot.warning(t(e.message_key))
    st.button(t("try_again"), key=f"retry_{pending['where']}", icon=":material/refresh:",
              on_click=retry, args=(pending,))


def run_pending(token: str, where: str) -> None:
    """Send the queued question (if it belongs here) and show the answer as it streams."""
    p = st.session_state.get("qa_pending")
    if not p or p["where"] != where:
        return
    text, msg_id = p["text"], p.get("id") or str(uuid.uuid4())
    with st.chat_message("user"):
        html(f'<p class="chat-text">{plain(text)}</p>')
    extra = {k: v for k, v in p.items() if k in ("force_intent", "state_code")}
    with st.chat_message("assistant", avatar=AVATAR):
        box = st.empty()  # holds the status line; cleared when a message replaces it (B-075)
        try:
            if where == "chat":
                body = {"message": text, "client_msg_id": msg_id, **extra}
                pin = st.session_state.get("pin")
                if pin and "state_code" not in body:  # the home page's location, if known
                    body |= {"lat": pin["lat"], "lng": pin["lng"]}
                done = show_stream(chat_stream(token, body), box)
            else:
                body = {"question": text, "client_msg_id": msg_id,
                        **{k: v for k, v in extra.items() if k == "force_intent"}}
                done = show_stream(ask_stream(token, where, body), box)
        except ApiError as e:  # limits, both AIs down, …: said in this bubble
            box.empty()
            st.session_state.pop("qa_pending", None)
            if e.retry_after:
                countdown(e, p)
            else:
                show_error(e, st.empty())
            return
        st.session_state.pop("qa_pending", None)
        if done is None:
            box.empty()
            st.error(t("server_error"))
            return
    # Reload from the server (F-02): its masked text, and turns from other tabs too.
    st.session_state.pop("home_chat", None)
    st.rerun()


def prompt(where: str, placeholder: str, key: str) -> None:
    typed = st.chat_input(placeholder, key=key, max_chars=300)
    if typed and typed.strip():
        queue(where, typed.strip())


def render_messages(token: str, where: str, messages: list[dict]) -> None:
    question = ""
    for i, m in enumerate(messages):
        with st.chat_message(m["role"], avatar=None if m["role"] == "user" else AVATAR):
            if m["role"] == "user":
                question = m.get("text", "")
                html(f'<p class="chat-text">{plain(question)}</p>')
            else:
                render_answer(token, where, m, question, active=i == len(messages) - 1)


# ---------- the two places ----------

def home_history(token: str) -> list[dict]:
    """GET /chat, kept until the next answer arrives (then reloaded, F-02). A refresh is a
    new session, so it rebuilds the chat too."""
    if "home_chat" not in st.session_state:
        try:
            st.session_state.home_chat = chat_history(token)
        except ApiError as e:
            if e.message_key == "login_required":
                show_error(e, st.empty())
            st.session_state.home_chat = []  # no history: the chat still works
    return st.session_state.home_chat


def render_home_chat(token: str) -> None:
    if st.session_state.pop("prefilled", False):
        st.toast(t("chat_prefilled"))
    html(f'<h2 class="section-title">{t("chat_title")}</h2>'
         f'<p class="muted page-help">{t("chat_help")}</p>')
    messages = home_history(token)
    if not messages:
        with st.container(horizontal=True, key="examples"):
            for i, ex in enumerate(CHAT_EXAMPLES):
                st.button(ex, key=f"example_{i}", on_click=queue, args=("chat", ex))
    render_messages(token, "chat", messages)
    prompt("chat", t("chat_placeholder"), "chat_text")
    run_pending(token, "chat")


def render_ask(token: str, rid: str) -> None:
    """The ask box at the end of a finished thread; the asks themselves are in the thread."""
    prompt(rid, t("qa_placeholder"), "ask_text")
    run_pending(token, rid)
