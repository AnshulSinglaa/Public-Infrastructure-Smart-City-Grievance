"""Login card (dev mode), the sidebar and the "My reports" page (UX §14)."""

import base64
import json
from datetime import datetime, timedelta, timezone
from html import escape

import streamlit as st
from streamlit_js_eval import streamlit_js_eval

from lib.api import ApiError, dev_login, me, my_reports
from report import (
    cat_name,
    go,
    html,
    open_dossier,
    page_title,
    pill,
    show_error,
    start_new_report,
)
from strings import t

TEXT_SIZES = {"100%": 16, "125%": 20, "150%": 24}  # root font size in px (body is 1rem)
STATE_DOT = {"dossier_ready": ":green[●]", "out_of_coverage": ":orange[●]",
             "needs_clarification": ":blue[●]", "rejected_junk": ":red[●]"}
IST = timezone(timedelta(hours=5, minutes=30))  # India has one time zone and no DST
# Names in the browser's localStorage. It survives a refresh; st.session_state does not.
TOKEN_KEY, DRAFT_KEY, PHOTO_KEY = "cgr_token", "cgr_draft", "cgr_photo"
# The photo is NOT blurred yet, so it goes in sessionStorage: it survives a refresh but the
# browser deletes it when the tab closes (SEC-012: a shared phone must not keep it).
STORE = {TOKEN_KEY: "localStorage", DRAFT_KEY: "localStorage", PHOTO_KEY: "sessionStorage"}
# localStorage holds about 5 MB per site; a bigger photo isn't kept (the user re-attaches).
MAX_KEPT_B64 = 1_500_000


def ist_date(iso: str) -> str:
    """'2026-10-28T21:05:00+00:00' (UTC from the API) -> '2026-10-29' (the date in India)."""
    return datetime.fromisoformat(iso).astimezone(IST).date().isoformat()


# ---------- keeping the login and the draft across a refresh (B-012) ----------
# streamlit_js_eval runs one JavaScript expression in the browser. It runs again only when
# the expression text changes, so saving the same token on every rerun costs nothing.

def browser_save(name: str, value: str) -> None:
    js = f"{STORE[name]}.setItem({json.dumps(name)}, {json.dumps(value)})"  # dumps = safe quotes
    streamlit_js_eval(js_expressions=js, want_output=False, key=f"save_{name}")


def browser_forget() -> None:
    js = "; ".join(f"{STORE[k]}.removeItem('{k}')" for k in (TOKEN_KEY, DRAFT_KEY, PHOTO_KEY))
    streamlit_js_eval(js_expressions=js, want_output=False, key="forget")


def photo_for_browser(photo: dict | None) -> str:
    """The photo draft as text for localStorage: its JPEG in base64 (B-028), or a "too big"
    marker so the page can say the photo wasn't kept. "" = no photo."""
    if not photo:
        return ""
    b64 = base64.b64encode(photo["bytes"]).decode()
    if len(b64) > MAX_KEPT_B64:
        return json.dumps({"too_big": True})
    return json.dumps({"name": photo["name"], "source": photo["source"], "b64": b64})


def photo_from_browser(kept: str) -> None:
    data = json.loads(kept)
    if data.get("too_big"):
        st.session_state.photo_lost = True  # the composer asks the user to re-attach
        return
    st.session_state.photo_draft = {"name": data["name"], "bytes": base64.b64decode(data["b64"]),
                                    "type": "image/jpeg", "source": data["source"]}


def restore_login() -> None:
    """On the login page: if the browser kept a token, check it once (GET /me, which is not
    rate-limited) and sign straight back in, with the draft text."""
    if st.session_state.get("signed_out"):  # the user signed out: wipe, never restore
        browser_forget()
        return
    if st.session_state.get("restore_done"):
        return
    keys = ", ".join(f"{STORE[k]}.getItem('{k}')" for k in (TOKEN_KEY, DRAFT_KEY, PHOTO_KEY))
    js = f"JSON.stringify([{keys}])"
    saved = streamlit_js_eval(js_expressions=js, key="restore")
    if saved is None:  # the browser hasn't answered yet; its answer triggers a rerun
        return
    st.session_state.restore_done = True
    token, draft, photo = json.loads(saved)
    if not token:
        return
    try:
        user = me(token)
    except ApiError:  # expired or unknown token: sign in as usual (that overwrites it)
        return
    st.session_state.update(token=token, user_name=user["name"])
    if draft:
        st.session_state.report_text = draft
    if photo:
        photo_from_browser(photo)
    st.rerun()


def sign_out() -> None:
    st.session_state.clear()  # forget the token and any draft in this session...
    st.session_state.signed_out = True  # ...and, on the login page, in the browser too


def render_login() -> None:
    with st.container(border=True, key="card_login"):
        st.markdown(f"### {t('login_title')}")
        st.caption(t("login_help"))
        st.caption(t("ai_notice"))  # SEC-014
        # A 401 from any page sends the user here with this note.
        if st.session_state.get("login_msg"):
            st.info(t(st.session_state.login_msg))
        with st.form("login", border=False):
            name = st.text_input(t("name_label"), max_chars=80, key="login_name")
            email = st.text_input(t("email_label"), max_chars=120, key="login_email")
            go_clicked = st.form_submit_button(t("continue"), type="primary", width="stretch",
                                               key="login_go")
        error = st.empty()
    if not go_clicked:
        return
    name, email = name.strip(), email.strip()
    if not name:
        error.error(t("need_name"))
        return
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        error.error(t("bad_email"))
        return
    try:
        result = dev_login(name, email)
    except ApiError as e:
        error.error(t(e.message_key))
        return
    st.session_state.token = result["token"]
    st.session_state.user_name = result["user"]["name"]
    st.session_state.pop("login_msg", None)
    st.session_state.pop("signed_out", None)
    st.rerun()


def reports(token: str) -> list[dict]:
    """My reports, loaded once and kept until a new report is made (report.py clears it)."""
    if "reports" not in st.session_state:
        st.session_state.reports = my_reports(token)
    return st.session_state.reports


def one_line(r: dict) -> str:
    """"Pothole · Ward 185". A report with no category yet (B-029) says what it waits for:
    "Needs your answer", "Photo rejected", …"""
    if not r.get("category"):
        return t(f"state_{r['state']}")
    where = f"Ward {r['ward_no']}" if r.get("ward_no") else t("state_out_of_coverage")
    return f"{cat_name(r['category'])} · {where}"


def open_report(token: str, r: dict, slot) -> None:
    try:
        st.session_state.pop("emergency", None)  # only shown right after reporting
        open_dossier(token, r["dossier_id"])
    except ApiError as e:
        show_error(e, slot)
        return
    st.rerun()


def report_clicked(token: str, r: dict, slot) -> None:
    if r.get("dossier_id"):
        open_report(token, r, slot)
    else:  # out of coverage: no dossier, show the helplines page again
        go("reports")
        st.rerun()


def render_sidebar(token: str, api_ok: bool) -> None:
    with st.sidebar:
        html(f'<p class="brand"><span class="brand-mark">◆</span> {t("app_name")}</p>')
        st.button(t("new_report"), key="nav_new", icon=":material/add:", width="stretch",
                  on_click=start_new_report)
        st.button(t("my_reports"), key="nav_reports", icon=":material/description:",
                  type="tertiary", width="stretch", on_click=go, args=("reports",))

        html(f'<p class="side-label">{t("recent")}</p>')
        slot = st.empty()
        try:
            recent = reports(token)[:5]
        except ApiError as e:
            show_error(e, slot)
            recent = []
        if not recent:
            st.caption(t("no_recent"))
        for r in recent:
            dot = STATE_DOT.get(r["state"], "○")
            if st.button(f"{dot} {one_line(r)}", key=f"recent_{r['report_id']}",
                         type="tertiary", width="stretch"):
                report_clicked(token, r, slot)

        # Pinned to the bottom by CSS (.st-key-side_user).
        with st.container(key="side_user", horizontal=True, vertical_alignment="center"):
            name = st.session_state.get("user_name", "?")
            status = "ok" if api_ok else "down"
            html(f'<div class="user"><span class="avatar">{escape(name[:1].upper())}</span>'
                 f'<span><span class="user-name">{escape(name)}</span><br>'
                 f'<span class="status {status}">● {t("connected" if api_ok else "offline")}'
                 f"</span></span></div>")
            with st.popover("", icon=":material/settings:", help=t("settings")):
                st.segmented_control(t("text_size"), list(TEXT_SIZES), default="100%",
                                     key="text_size")
                st.button(t("sign_out"), key="sign_out", icon=":material/logout:",
                          on_click=sign_out)


def render_reports(token: str) -> None:
    page_title(t("my_reports"), t("reports_help"))
    error = st.empty()
    st.session_state.pop("reports", None)  # always fresh on this page
    try:
        items = reports(token)
    except ApiError as e:
        show_error(e, error)
        return
    if not items:
        html(f'<div class="card"><p class="muted">{t("reports_empty")}</p></div>')
        return
    for r in items:
        with st.container(border=True, key=f"card_report_{r['report_id']}",
                          horizontal=True, vertical_alignment="center"):
            kind = "ok" if r["state"] == "dossier_ready" else "warn"
            pills = [pill(t(f"state_{r['state']}"), kind)]
            if r.get("department_code"):
                pills.append(pill(r["department_code"]))
            html(f'<div class="report-row"><p class="report-title">{escape(one_line(r))}</p>'
                 f'<p class="muted report-date">{escape(ist_date(r["created_at"]))}</p>'
                 f'<div class="pills">{"".join(pills)}</div></div>')
            # Out-of-coverage reports have no dossier to open.
            if r.get("dossier_id") and st.button(t("open"), key=f"open_{r['report_id']}",
                                                 icon=":material/arrow_forward:",
                                                 icon_position="right"):
                open_report(token, r, error)
