from pathlib import Path

import streamlit as st

from account import (
    DRAFT_KEY,
    PHOTO_KEY,
    TEXT_SIZES,
    TOKEN_KEY,
    browser_save,
    photo_for_browser,
    render_login,
    render_reports,
    render_sidebar,
    restore_login,
)
from lib.api import health
from report import render_dossier, render_form, render_uncovered
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


token = st.session_state.get("token")
if not token:  # login comes before anything else (no sidebar yet)
    restore_login()  # a refresh: sign back in with the token the browser kept
    render_login()
    st.stop()

# Keep the login and the draft text in the browser, so a refresh doesn't lose them.
browser_save(TOKEN_KEY, token)
browser_save(DRAFT_KEY, st.session_state.get("report_text") or "")
# The same large text on every rerun is cheap: Streamlit caches big messages by hash.
browser_save(PHOTO_KEY, photo_for_browser(st.session_state.get("photo_draft")))

render_sidebar(token, api_health())
view = st.session_state.get("view", "new")
if view == "reports":
    render_reports(token)
elif view == "dossier" and "current" in st.session_state:
    render_dossier(st.session_state.current)
elif view == "uncovered" and "uncovered" in st.session_state:
    render_uncovered(st.session_state.uncovered)
else:
    render_form(token)
