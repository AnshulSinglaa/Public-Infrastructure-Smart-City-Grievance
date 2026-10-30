"""New report composer, the dossier and the out-of-coverage screen (UX §14).

Which screen shows is `st.session_state.view`: "new" | "dossier" | "uncovered" | "reports".
"""

import io
from html import escape

import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError

from lib.api import ApiError, categories, clarify, create_report, get_dossier, get_photo
from location import (
    chip_label,
    gps_round_key,
    meters,
    read_gps_now,
    render_location_panel,
    reset_location,
    shaky,
    undo_pin,
)
from strings import t

MAX_PHOTO_BYTES = 5 * 1024 * 1024  # contract: photo <= 5 MB
# Shown on the dossier. None says "verified": the server can't check a phone's claim.
NOTICE_PILLS = ("low_gps_accuracy", "photo_unverified_location", "location_reported_by_phone",
                "photo_not_checked")
CPGRAMS = {"name": "CPGRAMS", "url": "https://pgportal.gov.in"}  # national grievance portal
# A new report forgets the draft and any question / retake / emergency left from the last one.
DRAFT_KEYS = ("report_text", "photo_draft", "attach", "landmark", "loc_open",
              "clarify", "clarify_other", "retake", "emergency")


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def pill(text: str, kind: str = "") -> str:
    # API text is escaped before it goes into raw HTML.
    return f'<span class="pill {kind}">{escape(text)}</span>'


def page_title(title: str, helper: str = "") -> None:
    html(f'<h1 class="page-title">{escape(title)}</h1>'
         + (f'<p class="muted page-help">{escape(helper)}</p>' if helper else ""))


def show_error(e: ApiError, slot) -> None:
    """Every page reports API errors here. A 401 means the token is gone: back to login."""
    if e.message_key == "login_required":
        st.session_state.pop("token", None)
        st.session_state.login_msg = "login_required"
        st.rerun()
    slot.error(t(e.message_key))


def go(view: str) -> None:
    st.session_state.view = view


def start_new_report() -> None:
    """Sidebar "+ New report" and the dossier's New report button (used as on_click)."""
    for key in DRAFT_KEYS:
        st.session_state.pop(key, None)
    reset_location()
    go("new")


def cat_name(code: str | None) -> str:
    """Label for a category code (UPPERCASE, data/categories.csv) from GET /categories.
    If the list can't be loaded, the code itself is made readable: POTHOLE -> "Pothole".
    A report that is still waiting (clarify / junk) has no category: never index on it."""
    if not code:
        return t("no_category")
    try:
        names = category_names(st.session_state.get("token", ""))
    except ApiError:
        names = {}
    return names.get(code) or code.replace("_", " ").capitalize()


def handle_step(token: str, report: dict) -> None:
    """POST /reports and POST /clarify both answer with this envelope: pick the next screen."""
    st.session_state.pop("reports", None)  # sidebar Recent reloads
    for key in ("clarify", "clarify_other", "retake"):
        st.session_state.pop(key, None)
    st.session_state.emergency = report.get("emergency")  # shown above whatever comes next
    state = report["state"]
    if state == "needs_clarification":
        st.session_state.clarify = {"report_id": report["report_id"], **report["next"]}
        go("new")
    elif state == "rejected_junk":
        st.session_state.retake = report["next"]["reason"]
        go("new")
    elif state == "out_of_coverage":  # no dossier, only general helplines
        # state_directory is at the TOP level of the response, not inside `next` (B-034).
        st.session_state.uncovered = {**report["next"],
                                      "state_directory": report.get("state_directory")}
        go("uncovered")
    else:
        open_dossier(token, report["dossier_id"], report.get("notices", []))


def open_dossier(token: str, dossier_id: str, notices=()) -> None:
    """Load a dossier and its BLURRED photo (never the raw upload), then show it."""
    d = get_dossier(token, dossier_id)
    photo = get_photo(token, d["photo_url"]) if d.get("photo_url") else None
    st.session_state.current = {"dossier": d, "photo": photo, "notices": list(notices)}
    go("dossier")


# ---------- composer ----------

def toggle_attach(kind: str) -> None:
    st.session_state.attach = None if st.session_state.get("attach") == kind else kind


def small_jpeg(data: bytes) -> bytes:
    """The photo as a JPEG of at most 1280 px (what the server keeps anyway), so the draft
    is small enough to keep in the browser. exif_transpose first: dropping EXIF must not
    turn a phone photo sideways. Not an image -> unchanged, and the server says so (422)."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except (UnidentifiedImageError, OSError):
        return data
    img.thumbnail((1280, 1280))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def keep_photo(widget_key: str, source: str) -> None:
    """Camera/Upload callback: copy the photo into the draft and close the input."""
    f = st.session_state.get(widget_key)
    if not f:
        return
    if f.size > MAX_PHOTO_BYTES:
        st.session_state.photo_error = "photo_too_large"
        return
    st.session_state.photo_draft = {"name": "photo.jpg", "bytes": small_jpeg(f.getvalue()),
                                    "type": "image/jpeg", "source": source}
    st.session_state.attach = None
    st.session_state.pop("photo_lost", None)


def remove_photo() -> None:
    st.session_state.pop("photo_draft", None)


def render_composer() -> tuple[str, bool, object]:
    """The one composer card. Returns (text, prepare clicked, error slot)."""
    with st.container(border=True, key="card_composer"):
        text = st.text_area(t("text_label"), placeholder=t("text_ph"), max_chars=1000,
                            height=110, key="report_text", label_visibility="collapsed")
        st.caption(t("privacy_hint"))  # names can't be masked automatically (B-022)
        st.caption(t("ai_notice_short"))  # SEC-014: who sees the report
        photo = st.session_state.get("photo_draft")
        if photo:
            with st.container(horizontal=True, vertical_alignment="center", key="thumb"):
                st.image(photo["bytes"], width=64)
                if photo["source"] == "upload":
                    st.caption(t("unverified"))
                st.button(t("remove_photo"), key="remove_photo", type="tertiary",
                          icon=":material/close:", on_click=remove_photo)

        attach = st.session_state.get("attach")
        if attach == "camera":
            st.camera_input(t("camera"), key="photo_camera", label_visibility="collapsed",
                            on_change=keep_photo, args=("photo_camera", "camera"))
        elif attach == "upload":
            st.file_uploader(t("upload"), type=["jpg", "jpeg", "png"], key="photo_upload",
                             label_visibility="collapsed",
                             on_change=keep_photo, args=("photo_upload", "upload"))

        if shaky():
            html(f'<div class="notice warn">{t("loc_shaky")}</div>')
        prev = st.session_state.get("pin_prev")
        if prev:  # the last map tap moved the pin: say how far, and offer Undo
            with st.container(horizontal=True, vertical_alignment="center", key="pin_moved"):
                st.caption(t("pin_moved", n=round(meters(prev, st.session_state.pin))))
                st.button(t("undo"), key="undo_pin", type="tertiary", on_click=undo_pin)
        error = st.empty()
        if st.session_state.get("photo_error"):
            error.error(t(st.session_state.pop("photo_error")))
        if st.session_state.get("photo_lost") and not photo:  # too big to keep (B-028)
            st.caption(t("photo_not_kept"))

        with st.container(horizontal=True, vertical_alignment="center", key="toolbar"):
            st.button(t("camera"), key="btn_camera", icon=":material/photo_camera:",
                      type="tertiary", on_click=toggle_attach, args=("camera",))
            st.button(t("upload"), key="btn_upload", icon=":material/upload:",
                      type="tertiary", on_click=toggle_attach, args=("upload",))
            st.button(chip_label(), key="loc_chip", icon=":material/location_on:",
                      on_click=lambda: st.session_state.update(loc_open=True))
            st.space("stretch")
            clicked = st.button(t("prepare"), key="prepare", type="primary",
                                icon=":material/arrow_forward:", icon_position="right")
    return text, clicked, error


def render_form(token: str) -> None:
    read_gps_now()
    page_title(t("new_title"), t("new_help"))
    if st.session_state.get("clarify"):  # the question card takes the composer's place
        render_clarify(token, st.session_state.clarify)
        return
    if st.session_state.get("retake"):
        render_retake(st.session_state.retake)
    text, clicked, error = render_composer()
    submit(token, text, clicked, error)
    if st.session_state.get("loc_open"):
        render_location_panel(token)


def submit(token: str, text: str, clicked: bool, error) -> None:
    pin = st.session_state.get("pin")
    photo = st.session_state.get("photo_draft")
    submit_fix = gps_round_key() + "_submit"

    if clicked:
        if not photo and not text.strip():
            error.error(t("need_photo_or_text"))
            return
        if not pin:
            error.error(t("need_pin"))
            st.session_state.loc_open = True  # open the panel so the user can set it
            return
        # Read GPS once more at submit, but only if the pin is still the raw GPS pin.
        if pin["source"] == "gps" and submit_fix not in st.session_state.get("gps_seen", ()):
            st.session_state.submitting = True
            st.session_state.drift_before = st.session_state.get("gps_drift", False)
            st.rerun()  # the second fix arrives on a later rerun, then we send (below)
    elif st.session_state.get("submitting"):
        # ponytail: if the browser never answers, this waits; the user can click Prepare again.
        if submit_fix not in st.session_state.get("gps_seen", ()):
            error.info(t("checking_place"))
            return
        st.session_state.submitting = False
        if st.session_state.get("gps_drift") and not st.session_state.drift_before:
            return  # the two fixes disagree: the "check the pin" warning is now showing
    else:
        return

    verified = bool(photo) and photo["source"] == "camera"  # only in-app camera photos count
    photo_part = (photo["name"], photo["bytes"], photo["type"]) if photo else None
    gps_pin = pin["source"] in ("gps", "gps_moved")
    send_pin = {**pin, "acc": pin["acc"] if gps_pin else None}
    try:
        with st.spinner(t("preparing")):
            report = create_report(token, text.strip(), photo_part, send_pin, verified)
            handle_step(token, report)
    except ApiError as e:  # e.g. ai_unavailable (503): the draft stays, just try again
        show_error(e, error)
        return
    st.rerun()


# ---------- clarify, retake, emergency ----------

def conf_word(confidence: float) -> str:
    return t("conf_word_high" if confidence >= 0.5 else "conf_word_low")


@st.cache_data(ttl=3600)  # the category list changes rarely: fetch it once an hour
def category_names(token: str) -> dict[str, str]:
    """{code: English name} from GET /categories. Codes stay exactly as the API sends them
    (UPPERCASE): they go back unchanged in POST /clarify."""
    return {c["code"]: c["name_en"] for c in categories(token)}


def answer(token: str, report_id: str, category: str, slot) -> None:
    try:
        with st.spinner(t("preparing")):
            handle_step(token, clarify(token, report_id, category))
    except ApiError as e:
        show_error(e, slot)
        return
    st.rerun()


def render_clarify(token: str, q: dict) -> None:
    """One chat-style question card (the first step toward the Sprint 5 chat)."""
    mm = q.get("mismatch")
    question = (t("mismatch_q", image=cat_name(mm["image"]).lower(),
                  text=cat_name(mm["text"]).lower()) if mm else t("clarify_q"))
    with st.container(border=True, key="card_clarify"):
        html(f'<div class="bot-line"><span class="bot-avatar">◆</span>'
             f'<div><p class="bot-q">{escape(question, quote=False)}</p>'
             f'<p class="muted bot-help">{t("clarify_help")}</p></div></div>')
        error = st.empty()
        with st.container(horizontal=True, key="clarify_options"):
            for opt in q["options"]:
                label = f"{cat_name(opt['category'])} · {conf_word(opt['confidence'])}"
                if st.button(label, key=f"opt_{opt['category']}"):
                    answer(token, q["report_id"], opt["category"], error)
            if q.get("allow_other"):
                st.button(t("something_else"), key="opt_other", type="tertiary",
                          on_click=lambda: st.session_state.update(clarify_other=True))
        if q.get("allow_other") and st.session_state.get("clarify_other"):
            try:
                names = category_names(token)
            except ApiError as e:
                show_error(e, error)
                return
            with st.container(horizontal=True, vertical_alignment="bottom", key="other_row"):
                cat = st.selectbox(t("pick_category"), list(names), format_func=names.get,
                                   key="other_category")
                if st.button(t("use_category"), key="use_other", type="primary"):
                    answer(token, q["report_id"], cat, error)


def retake_photo() -> None:
    for key in ("retake", "photo_draft"):
        st.session_state.pop(key, None)
    st.session_state.attach = "camera"  # open the camera straight away; the text stays


def render_retake(reason: str) -> None:
    with st.container(border=True, key="card_retake"):
        st.markdown(f"#### {t('retake_title')}")
        st.caption(f"{t('retake_' + reason)} {t('retake_keep_text')}")
        st.button(t("retake"), key="retake_btn", type="primary",
                  icon=":material/photo_camera:", on_click=retake_photo)


def render_emergency(em: dict) -> None:
    """Red-accent card above everything. It never blocks: the report continues below."""
    with st.container(border=True, key="card_emergency"):
        st.markdown(f"#### ⚠️ {t('emergency_call_first')}")
        st.caption(t("emergency_help"))
        with st.container(horizontal=True):
            for i, ch in enumerate(em.get("call_first", [])):
                st.link_button(ch["label"], ch["deep_link"], icon=":material/call:",
                               type="primary" if i == 0 else "secondary", width="stretch")


# ---------- dossier and out of coverage ----------

def call_buttons(channels: list[dict]) -> None:
    # One primary action per screen: the first channel (helpline call).
    for i, ch in enumerate(channels):
        st.link_button(t("call", n=ch["value"]), ch["deep_link"], icon=":material/call:",
                       type="primary" if i == 0 else "secondary")


def render_uncovered(nxt: dict) -> None:
    """Out of coverage (FR-3.2): say so plainly, claim no department, offer helplines.
    Outside Delhi our Delhi helplines can't help: show the state's portal (if verified)
    and CPGRAMS, the national portal."""
    if nxt.get("state_directory") or nxt.get("outside_delhi"):
        render_state_card(nxt.get("state_directory") or {})
        return
    with st.container(border=True, key="card_uncovered"):
        st.markdown(f"#### {t('uncovered_title')}")
        st.caption(t("uncovered_help"))
        with st.container(horizontal=True):
            call_buttons(nxt["channels"])
            st.button(t("new_report"), key="dossier_new", icon=":material/add:",
                      on_click=start_new_report)


def render_state_card(sd: dict) -> None:
    national = sd.get("national") or CPGRAMS
    verified = bool(sd.get("verified") and sd.get("portal_url"))
    with st.container(border=True, key="card_uncovered"):
        st.markdown(f"#### {t('outside_title')}")
        # Unknown or unverified state: only the national portal is safe to suggest.
        st.caption(t("state_help", state=sd["state"]) if verified else t("state_unknown_help"))
        with st.container(horizontal=True, key="state_actions"):
            if verified:
                st.link_button(sd["portal_name"], sd["portal_url"], type="primary",
                               icon=":material/open_in_new:",
                               help=t("state_portal", state=sd["state"]))
                if sd.get("helpline"):
                    st.link_button(t("call_helpline", n=sd["helpline"]),
                                   f"tel:{sd['helpline']}", icon=":material/call:")
            st.link_button(national["name"], national["url"], icon=":material/open_in_new:",
                           type="secondary" if verified else "primary")
            st.button(t("new_report"), key="dossier_new", icon=":material/add:",
                      on_click=start_new_report)


def render_dossier(cur: dict) -> None:
    d = cur["dossier"]
    page_title(t("dossier_title"), t("we_dont_send"))
    html('<div class="pills header-pills">'
         + pill(cat_name(d["category"])) + pill(t(f"sev_{d['severity']}"))
         + pill(t("state_dossier_ready"), "ok") + "</div>")

    # Department card: who fixes it, where, and the honest-limits pills.
    dept, pin, routing, deadline = d["department"], d.get("pin") or {}, d["routing"], d["deadline"]
    ward = t("ward", n=pin["ward_no"], name=pin.get("ward_name", "")) if pin.get("ward_no") else ""
    pills = [pill(t("conf_" + routing.get("confidence_label", "unsure")), "accent")]
    if routing.get("curated"):
        pills.append(pill(t("curated"), "accent"))
    if deadline.get("label") == "immediate":  # emergency: no N-day deadline (B-037)
        pills.append(pill(t("deadline_immediate"), "warn"))
    else:
        key = "deadline_suggested" if deadline.get("label") == "suggested" else "deadline"
        pills.append(pill(t(key, n=deadline["sla_days"])))
    html(f'<div class="card"><p class="label muted">{t("who_fixes")}</p>'
         f'<h3 class="dept">{escape(dept["name_en"])} '
         f'<span class="muted">· {escape(dept["code"])}</span></h3>'
         f'<p class="muted">{escape(ward)}</p><div class="pills">{"".join(pills)}</div></div>')

    # What you reported: the citizen's own words (any language) and the blurred photo.
    with st.container(border=True, key="card_reported"):
        st.markdown(f"#### {t('you_reported')}")
        if d.get("citizen_text"):
            html(f'<p class="citizen-text">{escape(d["citizen_text"])}</p>')
        if cur["photo"]:
            st.image(cur["photo"], width=240)
            st.caption(t("photo"))
        elif d.get("photo_withheld"):  # only when blurring failed (B-032), never a guess
            st.caption(t("photo_withheld"))
        elif d.get("photo_url"):  # the blurred copy exists but didn't load
            st.caption(t("photo_not_loaded"))

    with st.container(border=True, key="card_letter"):
        st.markdown(f"#### {t('complaint_letter')}")
        st.code(d["complaint"]["en"], language=None, wrap_lines=True)
        st.caption(t("copy_hint"))

    with st.container(horizontal=True, key="actions"):
        call_buttons(d.get("channels", []))
        st.button(t("new_report"), key="dossier_new", icon=":material/add:",
                  on_click=start_new_report)

    notices = [pill(t(n), "muted-pill") for n in cur["notices"] if n in NOTICE_PILLS]
    if notices:
        html(f'<div class="pills">{"".join(notices)}</div>')
    if routing.get("fake"):
        html(f'<div class="notice">{t("demo_ai")}</div>')
