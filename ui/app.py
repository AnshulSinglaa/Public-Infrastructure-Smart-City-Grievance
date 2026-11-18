import time
from pathlib import Path

import streamlit as st

from account import (
    DRAFT_KEY,
    PHOTO_KEY,
    TEXT_SIZES,
    THREAD_KEY,
    TOKEN_KEY,
    auth_mode,
    browser_save,
    photo_for_browser,
    render_login,
    render_reports,
    render_sidebar,
    restore_login,
)
from assistant import render_home_chat
from chat import render_thread
from lib.api import health
from report import render_dossier, render_form
from strings import t

st.set_page_config(page_title=t("app_name"), page_icon=":material/location_city:",
                   layout="centered")
css = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

# Streamlit sizes text in rem, so changing the root font size scales the whole page.
size = st.session_state.get("text_size") or "100%"
st.markdown(f"<style>html {{ font-size: {TEXT_SIZES[size]}px; }}</style>",
            unsafe_allow_html=True)


@st.cache_data(ttl=30)  # don't hit the API on every click; re-check every 30 s
def api_health() -> bool:
    return health()


WAKE_TRIES, WAKE_PAUSE_S = 9, 5  # up to ~2 min: Render's free plan needs ~1 min to boot


def wake_api() -> None:
    """Free hosting sleeps after 15 idle minutes, and the first visit wakes it. Wait here with
    a friendly message instead of letting the first click fail."""
    if st.session_state.get("api_awake"):
        return
    if not health():
        with st.status(t("waking_title")) as box:
            st.write(t("waking_body"))
            for _ in range(WAKE_TRIES):
                time.sleep(WAKE_PAUSE_S)
                if health(timeout=10):
                    break
            else:  # never came up: say so, and let the user retry
                box.update(label=t("waking_failed"), state="error")
                st.button(t("try_again"), key="wake_retry")
                st.stop()
            box.update(label=t("waking_done"), state="complete")
    st.session_state.api_awake = True


# The hosted demo covers 25 wards; say so before anyone types a far-away address.
st.markdown(f'<div class="notice">{t("demo_banner")}</div>', unsafe_allow_html=True)
wake_api()

token = st.session_state.get("token")
if not token:  # login comes before anything else (no sidebar yet)
    restore_login()  # a refresh: sign back in with the token the browser kept
    render_login()
    st.stop()

# Keep the login and the draft text in the browser, so a refresh doesn't lose them.
# Only the 7-day dev token: a Supabase token stays on the server (SEC-005).
if auth_mode() == "dev":
    browser_save(TOKEN_KEY, token)
browser_save(DRAFT_KEY, st.session_state.get("report_text") or "")
# The same large text on every rerun is cheap: Streamlit caches big messages by hash.
browser_save(PHOTO_KEY, photo_for_browser(st.session_state.get("photo_draft")))
browser_save(THREAD_KEY, st.session_state.get("thread") or "")

render_sidebar(token, api_health())
view = st.session_state.get("view", "new")
if view == "reports":
    render_reports(token)
elif view == "dossier" and "current" in st.session_state:
    render_dossier(st.session_state.current)
elif st.session_state.get("thread"):  # a sent report: its chat
    render_thread(token, st.session_state.thread)
else:  # home: the report composer, then the civic assistant chat under it (HLD-S6 §6A)
    render_form(token)
    render_home_chat(token)
