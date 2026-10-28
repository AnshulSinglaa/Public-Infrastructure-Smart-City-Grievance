from pathlib import Path

import streamlit as st

from i18n import t
from lib.api import health
from report import render_dossier, render_form, render_uncovered

APP_NAME = "Civic Grievance Router"
TEXT_SIZES = {"100%": 17, "150%": 25.5, "200%": 34}  # base body size is 17px (UX §13)

st.set_page_config(page_title=APP_NAME, page_icon="🌉", layout="centered")
css = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")
st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


@st.cache_data(ttl=30)  # don't hit the API on every click; re-check every 30 s
def api_health() -> bool:
    return health()


with st.sidebar:
    st.markdown(f'<p class="brand">🌉 {APP_NAME}</p>', unsafe_allow_html=True)
    lang_label = st.segmented_control(
        "Language", ["हिंदी", "English"], default="हिंदी", label_visibility="collapsed"
    )
    lang = "en" if lang_label == "English" else "hi"
    st.caption(t("tagline", lang))
    st.divider()
    page = st.radio("nav", ["new", "issues"], format_func=lambda k: t(f"nav_{k}", lang),
                    label_visibility="collapsed")
    st.divider()
    size = st.selectbox(t("text_size", lang), list(TEXT_SIZES), key="text_size")  # see B-011
    if api_health():
        st.markdown(f'<span class="pill ok">● {t("api_ok", lang)}</span>', unsafe_allow_html=True)
    else:
        st.markdown(f'<span class="pill down">✕ {t("api_down", lang)}</span>',
                    unsafe_allow_html=True)

# Streamlit sizes text in rem, so changing the root font size scales the whole page.
st.markdown(f"<style>html {{ font-size: {TEXT_SIZES[size]}px; }}</style>", unsafe_allow_html=True)

if page == "issues":
    st.markdown(f'<div class="card"><h2>{t("nav_issues", lang)}</h2>'
                f'<p class="muted">{t("soon", lang)}</p></div>', unsafe_allow_html=True)
elif "dossier" in st.session_state:
    render_dossier(st.session_state.dossier, lang)
elif "uncovered" in st.session_state:
    render_uncovered(st.session_state.uncovered, lang)
else:
    render_form(lang)
