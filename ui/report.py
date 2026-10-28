"""New report form (S03/S04) and the dossier screen (S10)."""

from html import escape

import streamlit as st

from i18n import both, t
from lib.api import ApiError, create_report, get_dossier

MAX_PHOTO_BYTES = 5 * 1024 * 1024  # contract: photo <= 5 MB
LANG_NAMES = {"hi": "हिंदी", "en": "English"}


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def pill(text: str, kind: str = "") -> str:
    # API text is escaped before it goes into raw HTML.
    return f'<span class="pill {kind}">{escape(text)}</span>'


def render_form(lang: str) -> None:
    html(f'<h2 class="page-title">{t("new_title", lang)}</h2>'
         f'<p class="muted">{t("new_help", lang)}</p>')

    with st.container(border=True, key="card_photo"):
        st.markdown(f"#### {t('step_photo', lang)}")
        source = st.segmented_control(
            t("step_photo", lang), ["camera", "gallery"], default="camera", key="photo_source",
            format_func=lambda k: t("take_photo" if k == "camera" else "from_gallery", lang),
            label_visibility="collapsed",
        )
        # Only one input is drawn at a time, so there is never a "which photo wins?" question.
        # Every input has a fixed key: without one, Streamlit identifies a widget by its
        # label, so a language switch made a new, empty widget and dropped the photo (B-011).
        if source == "gallery":
            photo = st.file_uploader(t("from_gallery", lang), type=["jpg", "jpeg", "png"],
                                     label_visibility="collapsed", key="photo_gallery")
            html(pill("⚠ " + t("unverified", lang), "warn"))
        else:
            photo = st.camera_input(t("take_photo", lang), label_visibility="collapsed",
                                    key="photo_camera")

    with st.container(border=True, key="card_text"):
        st.markdown(f"#### {t('step_text', lang)}")
        text = st.text_area(t("text_label", lang), placeholder=t("text_ph", lang),
                            max_chars=1000, key="report_text")
        st.caption(both("mic_hint"))

    error = st.empty()  # inline message slot, right above the button
    if not st.button(both("prepare"), type="primary", width="stretch", key="prepare"):
        return

    text = text.strip()
    if not photo and not text:
        error.error(t("need_photo_or_text", lang), icon="⚠️")
        return
    if photo and photo.size > MAX_PHOTO_BYTES:
        error.error(t("photo_too_large", lang), icon="⚠️")
        return

    photo_part = (photo.name, photo.getvalue(), photo.type) if photo else None
    try:
        with st.spinner(t("preparing", lang)):
            report = create_report(text, photo_part, lang)
            if report["state"] == "out_of_coverage":  # no dossier, only general helplines
                st.session_state.uncovered = report["next"]["channels"]
            else:
                st.session_state.dossier = get_dossier(report["dossier_id"])
                st.session_state.photo = photo_part[1] if photo_part else None
    except ApiError as e:
        error.error(t(e.message_key, lang), icon="⚠️")
        return
    st.rerun()


def call_buttons(channels: list[dict], lang: str) -> None:
    # One primary action per screen: the first channel (helpline call).
    for i, ch in enumerate(channels):
        st.link_button(f"📞 {t('call', lang)} {ch['value']}", ch["deep_link"],
                       type="primary" if i == 0 else "secondary", width="stretch")


def new_another_button(lang: str) -> None:
    if st.button(t("new_another", lang), width="stretch", key="new_another"):
        for key in ("dossier", "uncovered", "photo", "report_text"):
            st.session_state.pop(key, None)
        st.rerun()


def render_uncovered(channels: list[dict], lang: str) -> None:
    """Out of coverage (FR-3.2): say so plainly, claim no department, offer helplines."""
    html(f'<div class="card"><h2>📍 {t("uncovered_title", lang)}</h2>'
         f'<p class="muted">{t("uncovered_help", lang)}</p></div>')
    call_buttons(channels, lang)
    new_another_button(lang)


def render_dossier(d: dict, lang: str) -> None:
    html(f'<h2 class="page-title">✅ {t("dossier_title", lang)}</h2>'
         f'<p class="muted">{t("we_dont_send", lang)}</p>')

    # Department card: who fixes it, where, and the honest-limits pills.
    dept = d["department"]
    name = dept["name_hi"] if lang == "hi" else dept["name_en"]
    pin = d.get("pin") or {}
    ward = t("ward", lang, n=pin["ward_no"], name=pin.get("ward_name", "")) \
        if pin.get("ward_no") else ""
    deadline = d["deadline"]
    deadline_key = "deadline_suggested" if deadline.get("label") == "suggested" else "deadline"
    pills = [
        pill(t(f"cat_{d['category']}", lang)),
        pill(t(f"sev_{d['severity']}", lang)),
        pill("⏱ " + t(deadline_key, lang, n=deadline["sla_days"]), "accent"),
    ]
    if d["routing"].get("curated"):
        pills.append(pill("🏷 " + t("curated", lang), "accent"))
    html(
        f'<div class="card"><p class="muted label">{t("who_fixes", lang)}</p>'
        f'<h3 class="dept">🏛 {escape(name)} <span class="muted">· {escape(dept["code"])}</span>'
        f'</h3><p class="muted">📍 {escape(ward)}</p>'
        f'<div class="pills">{"".join(pills)}</div></div>'
    )

    with st.container(border=True, key="card_complaint"):
        st.markdown(f"#### {t('complaint_text', lang)}")
        order = ["hi", "en"] if lang == "hi" else ["en", "hi"]  # UI language opens first
        for tab, code in zip(st.tabs([LANG_NAMES[c] for c in order]), order):
            with tab:
                st.code(d["complaint"][code], language=None, wrap_lines=True)
        st.caption(t("copy_hint", lang))

    if st.session_state.get("photo"):
        with st.container(border=True, key="card_photo_view"):
            st.markdown(f"#### {t('your_photo', lang)}")
            st.image(st.session_state.photo, width="stretch")

    call_buttons(d.get("channels", []), lang)
    new_another_button(lang)

    if d["routing"].get("fake"):
        html(f'<div class="notice">🧪 {t("demo_ai", lang)}</div>')
