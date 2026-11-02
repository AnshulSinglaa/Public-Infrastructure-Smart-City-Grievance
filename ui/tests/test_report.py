import io
import re
from html import escape
from pathlib import Path

import httpx
import pytest
from PIL import Image  # comes with Streamlit
from streamlit.testing.v1 import AppTest

import location
from lib.api import ApiError, _check  # `ui` is on pytest's pythonpath (pyproject.toml)
from lib.fake_api import TOKEN
from location import merge_fix
from strings import TEXT

UI_DIR = Path(__file__).resolve().parents[1]
BACKEND_APP = UI_DIR.parent / "backend" / "app"
PIN = {"lat": 28.544, "lng": 77.311, "source": "map_click", "acc": None, "label": ""}


def tiny_png(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


PNG = tiny_png("gray")  # what the user uploads
BLURRED = tiny_png("black")  # what the server serves back


def page_text(at) -> str:
    return " ".join(m.value for m in at.markdown)


@pytest.fixture
def new_app(monkeypatch):
    monkeypatch.setenv("FAKE_API", "1")  # canned backend, see ui/lib/fake_api.py
    return AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=30)


@pytest.fixture
def app(new_app):
    """Signed in, on the new-report composer, no pin yet."""
    new_app.session_state["token"] = TOKEN
    new_app.session_state["user_name"] = "Priya"
    new_app.run()
    assert not new_app.exception
    return new_app


def submit(app, text, pin=PIN):
    if pin:
        app.session_state["pin"] = dict(pin)
    app.text_area(key="report_text").input(text).run()
    app.button(key="prepare").click().run()
    assert not app.exception


def open_letter(app):
    """Click "Open full letter" in the chat's dossier bubble."""
    [btn] = [b for b in app.button if b.key.startswith("open_letter_")]
    btn.click().run()
    assert not app.exception and app.session_state["view"] == "dossier"


def reply(app, value):
    app.button(key=f"reply_{value}").click().run()
    assert not app.exception and not app.error


def type_answer(app, text):
    app.chat_input(key="answer_text").set_value(text).run()
    assert not app.exception and not app.error


def upload_photo(app):
    app.button(key="btn_upload").click().run()
    app.file_uploader(key="photo_upload").upload("p.png", PNG, "image/png").run()
    assert app.session_state["photo_draft"]["source"] == "upload"


def test_login_flow(new_app):
    new_app.run()
    assert "report_text" not in new_app.session_state  # login first, no composer yet
    new_app.button(key="login_go").click().run()
    assert [e.value for e in new_app.error] == [TEXT["need_name"]]
    new_app.text_input(key="login_name").input("Priya")  # sent with the form
    new_app.button(key="login_go").click().run()
    assert new_app.session_state["token"] == TOKEN
    assert new_app.text_area(key="report_text")  # now on the composer


def test_bad_token_goes_back_to_login(new_app):
    new_app.session_state["token"] = "expired"
    new_app.run()  # the sidebar's Recent list gets a 401
    assert "token" not in new_app.session_state
    assert TEXT["login_required"] in [i.value for i in new_app.info]


def test_empty_submit_shows_error(app):
    app.button(key="prepare").click().run()
    assert [e.value for e in app.error] == [TEXT["need_photo_or_text"]]


def test_submit_without_pin_shows_need_pin_and_opens_panel(app):
    submit(app, "Bada gaddha hai", pin=None)
    assert [e.value for e in app.error] == [TEXT["need_pin"]]
    assert app.session_state["loc_open"]


def test_location_chip_states(app):
    assert TEXT["locating"] in app.button(key="loc_chip").label
    app.session_state["pin"] = {**PIN, "source": "gps", "acc": 12}
    app.run()
    assert app.button(key="loc_chip").label == "Your location · ±12 m · Change"


def test_landmark_pick_sets_pin(app):
    app.button(key="loc_chip").click().run()  # opens the location panel
    app.text_input(key="landmark").input("sarkari school")  # inside a form: sent on submit
    app.button(key="find_landmark").click().run()
    app.button(key="cand_1").click().run()
    pin = app.session_state["pin"]
    assert pin["source"] == "landmark" and pin["label"] == "Sarita Vihar Metro Station"
    assert "candidates" not in app.session_state  # the list closes after a pick
    assert app.button(key="loc_chip").label.startswith("Sarita Vihar Metro Station")


def test_landmark_not_found(app):
    app.button(key="loc_chip").click().run()
    app.text_input(key="landmark").input("nothing here")
    app.button(key="find_landmark").click().run()
    assert [i.value for i in app.info] == [TEXT["landmark_none"]]


def test_nudge_moves_pin_about_10_m(app):
    app.session_state["pin"] = {**PIN, "source": "gps", "acc": 12}
    app.session_state["loc_open"] = True
    app.run()
    app.button(key="nudge_↑").click().run()
    pin = app.session_state["pin"]
    assert pin["source"] == "gps_moved"
    assert abs((pin["lat"] - PIN["lat"]) * 111_320 - 10) < 0.01


def test_merge_fix_keeps_best_and_flags_drift():
    a = {"lat": 28.5, "lng": 77.3, "acc": 20}
    b = {"lat": 28.5001, "lng": 77.3, "acc": 10}  # ~11 m north, more accurate
    assert merge_fix(None, a) == (a, False)
    assert merge_fix(a, b) == (b, False)
    far = {"lat": 28.5005, "lng": 77.3, "acc": 25}  # ~56 m away: more than 30 m and acc
    assert merge_fix(a, far) == (a, True)
    # Two rough fixes (±80 m) 56 m apart are GPS noise, not drift (TL finding #5).
    rough_a, rough_b = {**a, "acc": 80}, {**far, "acc": 80}
    assert merge_fix(rough_a, rough_b) == (rough_a, False)


def fake_gps(monkeypatch, fixes: dict):
    """Make get_geolocation answer at once, per component key, like a browser would."""
    def answer(component_key):
        lat, lng, acc = fixes[component_key.split("_", 2)[-1] if "submit" in component_key
                              else "open"]
        return {"coords": {"latitude": lat, "longitude": lng, "accuracy": acc}}
    monkeypatch.setattr(location, "get_geolocation", answer)


def test_second_gps_fix_at_submit_sends_better_pin(new_app, monkeypatch):
    fake_gps(monkeypatch, {"open": (28.5440, 77.311, 40), "submit": (28.54405, 77.311, 8)})
    new_app.session_state["token"] = TOKEN
    new_app.run()
    assert new_app.session_state["pin"]["acc"] == 40  # first fix, on open
    new_app.text_area(key="report_text").input("gaddha").run()
    new_app.button(key="prepare").click().run()
    assert not new_app.exception and not new_app.error
    assert new_app.session_state["pin"]["acc"] == 8  # the better fix won
    assert new_app.session_state["thread"]  # sent: the page is now its chat


def test_second_gps_fix_that_drifts_stops_and_warns(new_app, monkeypatch):
    fake_gps(monkeypatch, {"open": (28.5440, 77.311, 10), "submit": (28.5450, 77.311, 10)})
    new_app.session_state["token"] = TOKEN
    new_app.run()
    new_app.text_area(key="report_text").input("gaddha").run()
    new_app.button(key="prepare").click().run()
    assert "thread" not in new_app.session_state  # not sent: ~111 m apart
    assert TEXT["loc_shaky"] in page_text(new_app)
    new_app.button(key="prepare").click().run()  # user checked the pin; second click sends
    assert new_app.session_state["thread"]


def test_text_submit_shows_chat_then_full_letter(app):
    submit(app, "Bada gaddha hai school ke saamne")
    assert not app.error
    page = page_text(app)
    assert "Bada gaddha hai school ke saamne" in page  # the user's bubble, as written
    assert "Municipal Corporation of Delhi" in page  # the dossier bubble
    assert "narrower than 60 ft, so Municipal Corporation of Delhi" in page  # the why line
    assert "Road width" in page and "Zone:" not in page  # evidence pills, as the real API
    assert "AI is simulated" in page
    open_letter(app)
    page = page_text(app)
    assert "Curated data" in page and "15 days" in page and "Very likely" in page
    [letter] = app.code
    assert letter.value.startswith("To, Municipal")  # English letter only
    app.button(key="back_to_chat").click().run()
    assert app.session_state["view"] == "new" and app.session_state["thread"]


def test_dossier_shows_blurred_photo_not_raw_upload(app, monkeypatch):
    import report

    calls = []
    monkeypatch.setattr(report, "get_photo", lambda tok, url: calls.append(url) or BLURRED)
    upload_photo(app)
    submit(app, "kooda")
    open_letter(app)
    assert calls == ["/api/v1/media/00000000-0000-0000-0000-00000000d055"]
    assert app.session_state["current"]["photo"] == BLURRED
    assert "Photo location not verified" in page_text(app)  # uploaded, not camera


def test_withheld_photo_says_so(app):
    upload_photo(app)
    submit(app, "kooda #withheld")
    open_letter(app)
    assert TEXT["photo_withheld"] in [c.value for c in app.caption]


def test_remove_photo(app):
    upload_photo(app)
    app.button(key="remove_photo").click().run()
    assert "photo_draft" not in app.session_state


def test_rate_limit_shows_message(app):
    submit(app, "gaddha #429")
    assert [e.value for e in app.error] == [TEXT["rate_limited_daily"]]


def test_my_reports_opens_the_thread(app):
    app.button(key="nav_reports").click().run()
    page = page_text(app)
    assert "Pothole · Ward 185" in page and "MCD" in page and "2026-10-26" in page
    app.button(key="open_00000000-0000-0000-0000-0000000000a0").click().run()
    assert not app.exception
    assert app.session_state["thread"] == "00000000-0000-0000-0000-0000000000a0"
    assert "Sadak pe bada gaddha hai" in page_text(app)  # its chat, from GET /conversation


def test_sidebar_recent_opens_the_thread(app):
    recent = [b for b in app.sidebar.button if b.key.startswith("recent_")]
    assert 1 <= len(recent) <= 5
    recent[-1].click().run()
    assert app.session_state["thread"] == recent[-1].key.removeprefix("recent_")
    assert [m for m in app.chat_message]  # the thread's bubbles


def test_text_size_change_keeps_pin_and_photo(app):
    upload_photo(app)
    kept = app.session_state["photo_draft"]["bytes"]
    app.session_state["pin"] = dict(PIN)
    app.run()
    app.segmented_control(key="text_size").set_value("150%").run()
    assert app.session_state["pin"] == PIN
    assert app.session_state["photo_draft"]["bytes"] == kept


def test_every_api_error_key_has_text():
    # Every message_key the backend can send, read from its source, so a new key fails here.
    src = " ".join(f.read_text(encoding="utf-8") for f in BACKEND_APP.rglob("*.py"))
    keys = set(re.findall(r'error\(\d+, "\w+", "(\w+)"\)|"message_key": "(\w+)"', src))
    keys = {k for pair in keys for k in pair if k}
    assert "photo_not_jpeg_png" in keys and "not_found" in keys  # the regex still works
    assert keys <= TEXT.keys()


def test_every_ui_text_key_exists():
    # Every literal t("key") in the UI code has a line in strings.py (SEC-014 included).
    src = " ".join(f.read_text(encoding="utf-8") for f in UI_DIR.glob("*.py"))
    keys = set(re.findall(r'(?<![\w.])t\("(\w+)"', src))
    keys = {k for k in keys if not k.endswith("_")}  # t("conf_" + x): a prefix, completed later
    assert {"ai_notice", "ai_notice_short", "privacy_hint"} <= keys  # the scan still works
    assert keys <= TEXT.keys()


def test_ai_notice_on_sign_in_and_composer(monkeypatch):  # SEC-014
    monkeypatch.setenv("FAKE_API", "1")
    login = AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=30)
    login.run()  # not signed in: the sign-in card
    assert TEXT["ai_notice"] in [c.value for c in login.caption]
    composer = AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=30)
    composer.session_state["token"] = TOKEN
    composer.run()
    assert TEXT["ai_notice_short"] in [c.value for c in composer.caption]


def test_sprint3_error_keys_have_text():
    for key in ("login_required", "need_pin", "rate_limited_daily", "rate_limited_same_spot",
                "rate_limited_cooldown", "geocode_failed"):
        assert key in TEXT


def test_unknown_error_key_falls_back_to_generic():
    with pytest.raises(ApiError) as e:
        _check(httpx.Response(422, json={"error": {"message_key": "brand_new_key"}}))
    assert e.value.message_key == "server_error"


def test_out_of_coverage_shows_helplines_not_a_department(app):
    submit(app, "gaddha", pin={**PIN, "lat": 28.6129, "lng": 77.2295})  # India Gate
    assert not app.error
    assert TEXT["uncovered_title"] in page_text(app)
    assert TEXT["who_fixes"] not in page_text(app)  # no department claim
    [call] = app.get("link_button")
    assert call.proto.url == "tel:155305" and "155305" in call.proto.label


class FakeBrowser:
    """Stands in for streamlit_js_eval: answers the "restore" read with what the browser
    kept, and records every script the app asks the browser to run."""

    def __init__(self, saved):
        self.saved, self.ran = saved, []

    def __call__(self, js_expressions, key, want_output=True):
        self.ran.append(js_expressions)
        return self.saved if key == "restore" else None


def test_refresh_restores_login_and_draft(new_app, monkeypatch):  # B-012
    import account

    browser = FakeBrowser('["fake-token", "bada gaddha, half typed", null]')
    monkeypatch.setattr(account, "streamlit_js_eval", browser)
    new_app.run()  # a new session, as after F5: no token in session_state
    assert not new_app.exception
    assert new_app.session_state["token"] == TOKEN  # checked with GET /me, no new login
    assert new_app.session_state["user_name"] == "Priya"
    assert new_app.text_area(key="report_text").value == "bada gaddha, half typed"
    assert any("setItem(\"cgr_token\", \"fake-token\")" in js for js in browser.ran)


def test_sign_out_wipes_the_browser_copy(app, monkeypatch):  # B-012
    import account

    browser = FakeBrowser('["fake-token", "", null]')
    monkeypatch.setattr(account, "streamlit_js_eval", browser)
    app.button(key="sign_out").click().run()
    assert "token" not in app.session_state  # and the kept token is NOT restored
    assert any("removeItem('cgr_token')" in js and "removeItem('cgr_photo')" in js
               for js in browser.ran)


def test_map_tap_says_pin_moved_and_undo_restores(app):  # B-021
    old = {**PIN, "source": "landmark", "label": "Kalindi Kunj"}
    app.session_state["pin"] = {**PIN, "lat": PIN["lat"] + 0.001, "source": "map_click"}
    app.session_state["pin_prev"] = old  # what location.on_map_click leaves after a tap
    app.run()
    assert "Pin moved 111 m" in [c.value for c in app.caption]
    app.button(key="undo_pin").click().run()
    assert app.session_state["pin"] == old and "pin_prev" not in app.session_state


MUMBAI = {**PIN, "lat": 19.07, "lng": 72.87}


def links(app) -> dict:
    return {b.proto.label: b.proto.url for b in app.get("link_button")}


def test_outside_delhi_shows_state_portal_helpline_and_cpgrams(app):  # B-018, Sprint 4
    submit(app, "gaddha", pin=MUMBAI)
    page = page_text(app)
    assert TEXT["outside_title"] in page and "Maharashtra" in page
    assert "anywhere in Delhi" not in page  # no Delhi helplines outside Delhi
    assert links(app) == {"Aaple Sarkar": "https://grievances.maharashtra.gov.in",
                          "Call 1800120800": "tel:1800120800",
                          "CPGRAMS": "https://pgportal.gov.in"}


def test_unverified_state_shows_cpgrams_only(app):
    submit(app, "gaddha #unverified", pin=MUMBAI)
    assert links(app) == {"CPGRAMS": "https://pgportal.gov.in"}
    assert "national portal" in page_text(app)


def test_clarify_category_is_answered_in_the_chat(app):
    submit(app, "kuch toh hai #clarify")
    assert "We're not sure — which is it?" in page_text(app)
    assert "report_text" not in [w.key for w in app.text_area]  # the chat replaces the composer
    assert app.button(key="reply_POTHOLE").label == "Pothole · likely"
    reply(app, "POTHOLE")
    assert "Municipal Corporation of Delhi" in page_text(app)  # dossier bubble


def test_mismatch_question_names_both(app):
    submit(app, "light kharab hai #mismatch")
    assert ("Your photo looks like garbage dump, your text says streetlight not working"
            in page_text(app))
    reply(app, "STREETLIGHT_OUT")
    assert TEXT["who_fixes"] in page_text(app)


def test_retake_keeps_text_and_opens_camera(app):
    submit(app, "gaddha hai #junk_selfie")
    assert TEXT["retake_selfie"] in page_text(app)
    app.button(key="retake_btn").click().run()
    assert app.session_state["report_text"] == "gaddha hai #junk_selfie"
    assert app.session_state["attach"] == "camera" and "thread" not in app.session_state


def test_emergency_card_comes_first(app):
    submit(app, "taar gira hua hai #emergency")
    page = page_text(app)
    assert page.index(TEXT["emergency_call_first"]) < page.index("taar gira hua hai")
    assert page.index("taar gira hua hai") < page.index(TEXT["who_fixes"])  # chat continues
    assert list(links(app).values())[:2] == ["tel:112", "tel:19123"]  # call-first come first
    # B-047: every call button shows its number (first, so a narrow phone can't cut it off)
    assert list(links(app))[:2] == ["Emergency 112", "19123 · BSES Rajdhani"]


def test_ai_unavailable_keeps_the_draft(app):
    submit(app, "gaddha #503")
    assert [e.value for e in app.error] == [TEXT["ai_unavailable"]]
    assert app.session_state["report_text"] == "gaddha #503"
    assert "thread" not in app.session_state  # nothing was sent: the composer stays


def test_sprint4_keys_have_text():
    for key in ("invalid_category", "wrong_step", "ai_unavailable", "retake_selfie",
                "retake_meme", "retake_screenshot", "retake_blank", "retake_unrelated",
                "emergency_call_first", "state_portal"):
        assert key in TEXT


def test_camera_photo_is_never_called_verified(app):  # B-013
    app.session_state["photo_draft"] = {"name": "c.jpg", "bytes": PNG, "type": "image/png",
                                        "source": "camera"}
    submit(app, "gaddha")
    assert TEXT["location_reported_by_phone"] in page_text(app)


def test_composer_warns_not_to_type_name_or_address(app):  # B-022
    assert TEXT["privacy_hint"] in [c.value for c in app.caption]


def test_dates_are_shown_in_ist():  # B-026
    from account import ist_date
    assert ist_date("2026-10-28T21:05:00+00:00") == "2026-10-29"  # 02:35 IST on the 29th
    assert ist_date("2026-10-26T09:30:00+05:30") == "2026-10-26"


def test_upload_is_shrunk_to_a_1280px_jpeg():  # B-028: small enough to keep in the browser
    from report import small_jpeg
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000), "gray").save(big, "PNG")
    out = Image.open(io.BytesIO(small_jpeg(big.getvalue())))
    assert out.format == "JPEG" and max(out.size) == 1280


def test_refresh_keeps_the_photo(new_app, monkeypatch):  # B-028
    import json

    import account

    photo = {"name": "photo.jpg", "bytes": PNG, "type": "image/jpeg", "source": "camera"}
    kept = account.photo_for_browser(photo)
    browser = FakeBrowser(json.dumps(["fake-token", "", kept]))
    monkeypatch.setattr(account, "streamlit_js_eval", browser)
    new_app.run()
    draft = new_app.session_state["photo_draft"]
    assert draft["bytes"] == PNG and draft["source"] == "camera"  # camera claim kept too
    assert any("cgr_photo" in js and "setItem" in js for js in browser.ran)  # saved again


def test_refresh_with_a_too_big_photo_asks_to_reattach(new_app, monkeypatch):  # B-028
    import json

    import account

    huge = {"name": "p.jpg", "bytes": b"x" * 1_200_000, "type": "image/jpeg", "source": "upload"}
    marker = account.photo_for_browser(huge)  # 1.6 MB of base64: over the limit
    assert marker == json.dumps({"too_big": True})
    monkeypatch.setattr(account, "streamlit_js_eval",
                        FakeBrowser(json.dumps(["fake-token", "gaddha", marker])))
    new_app.run()
    assert "photo_draft" not in new_app.session_state
    assert TEXT["photo_not_kept"] in [c.value for c in new_app.caption]


# ---------- Sprint 4 fixes ----------

def test_reports_without_a_category_dont_crash_any_page(app, new_app):  # B-029
    submit(app, "kuch problem hai yahan #clarify")  # -> needs_clarification, no category
    new_app.session_state["token"] = TOKEN
    new_app.run()  # a fresh page load: the sidebar's Recent list renders first
    assert not new_app.exception
    labels = [b.label for b in new_app.sidebar.button if b.key.startswith("recent_")]
    assert any(TEXT["state_needs_clarification"] in label for label in labels)
    new_app.button(key="nav_reports").click().run()
    assert not new_app.exception and TEXT["state_needs_clarification"] in page_text(new_app)


def test_tracebacks_are_never_shown():  # B-030
    import tomllib

    from streamlit.config import ShowErrorDetailsConfigOptions as Options
    config = tomllib.loads((UI_DIR / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["showErrorDetails"] == Options.NONE  # not the legacy `false`


def test_unexpected_api_answer_shows_the_generic_message(monkeypatch):  # B-030
    from lib import api

    monkeypatch.setattr(api, "_client", lambda token=None: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>oops")),
        base_url="http://x"))
    with pytest.raises(ApiError) as e:
        api.my_reports("t")
    assert e.value.message_key == "server_error"


def test_photo_kept_when_the_vision_model_was_down(app, monkeypatch):  # B-032
    import report

    monkeypatch.setattr(report, "get_photo", lambda tok, url: BLURRED)
    upload_photo(app)
    submit(app, "kooda #photo_down")
    open_letter(app)
    captions = [c.value for c in app.caption]
    assert TEXT["photo_withheld"] not in captions and TEXT["photo"] in captions
    assert escape(TEXT["photo_not_checked"]) in page_text(app)  # pills are HTML-escaped


def test_state_portal_is_read_from_the_top_level(app):  # B-034
    submit(app, "gaddha", pin={**PIN, "lat": 19.07, "lng": 72.87})  # Maharashtra
    urls = [b.proto.url for b in app.get("link_button")]
    assert "https://grievances.maharashtra.gov.in" in urls and "https://pgportal.gov.in" in urls


def test_emergency_dossier_shows_immediate_action_not_days(app):  # B-037
    from lib.fake_api import DOSSIER

    app.session_state["current"] = {"photo": None, "notices": [], "dossier": {
        **DOSSIER, "category": "LIVE_WIRE", "severity": "critical",
        "deadline": {"sla_days": None, "label": "immediate"}}}
    app.session_state["view"] = "dossier"
    app.run()
    page = page_text(app)
    assert TEXT["deadline_immediate"] in page and "days" not in page.split("Who fixes")[-1][:400]


# ---------- Sprint 5: chat intake ----------

def test_road_question_answered_by_button(app):
    submit(app, "gaddha hai #road_q")
    assert "Is this on a main road or an inner lane?" in page_text(app)
    assert TEXT["who_fixes"] not in page_text(app)  # paused: no department yet
    reply(app, "inner_lane")
    page = page_text(app)
    assert "Inner lane / gali" in page  # the answer became the user's bubble
    assert TEXT["who_fixes"] in page
    # B-049: like the real API, the answer is the evidence (not the map's width or a zone)
    assert TEXT["ev_user_answer"] in page and "Road width" not in page and "Zone:" not in page
    assert not [b for b in app.button if b.key.startswith("reply_")]  # question closed


def test_discom_question_answered_by_free_text(app):
    submit(app, "light kharab hai #discom_q")
    assert "Which company sends your electricity bill?" in page_text(app)
    type_answer(app, "bses wala, laal logo")
    page = page_text(app)
    assert "bses wala, laal logo" in page and TEXT["who_fixes"] in page


def test_unclear_answer_is_asked_again(app):
    submit(app, "gaddha #unclear")
    type_answer(app, "pata nahi yaar")
    page = page_text(app)
    assert TEXT["answer_unclear"] in page  # a gentle re-ask, same question
    assert page.count("Is this on a main road or an inner lane?") == 2
    reply(app, "main_road")  # the quick replies are still there
    assert TEXT["who_fixes"] in page_text(app)


def test_not_sure_leads_to_pick_candidate(app):
    submit(app, "gaddha #road_q")
    reply(app, "not_sure")
    page = page_text(app)
    assert "More than one department could handle this. Which fits?" in page
    # B-046: each card says why and how sure, so the user can choose
    assert "so Public Works Department (GNCTD) maintains it" in page
    assert "may also be responsible here" in page
    assert TEXT["conf_likely"] in page and TEXT["conf_possible"] in page
    assert app.button(key="reply_PWD").label == "Public Works Department (GNCTD)"
    reply(app, "PWD")
    assert TEXT["who_fixes"] in page_text(app)


def test_pick_candidate_switch(app):
    submit(app, "Mathura Road pe gaddha #pick")
    reply(app, "NHAI")
    page = page_text(app)  # B-049: the dossier is for the department the user chose
    assert "You chose National Highways Authority of India. It may also be" in page
    assert "NHAI" in page and page.count(TEXT["ev_user_answer"]) == 1


def test_refresh_rebuilds_the_thread(app, new_app, monkeypatch):
    import json

    import account

    submit(app, "gaddha #road_q")
    thread = app.session_state["thread"]
    monkeypatch.setattr(account, "streamlit_js_eval",
                        FakeBrowser(json.dumps(["fake-token", "", None, thread])))
    new_app.run()  # F5: a new session, only the browser's copy is left
    assert not new_app.exception
    assert new_app.session_state["thread"] == thread
    assert "Is this on a main road or an inner lane?" in page_text(new_app)
    reply(new_app, "main_road")  # and it can still be answered
    assert TEXT["who_fixes"] in page_text(new_app)


def test_sprint5_keys_have_text():
    # The question and "why" texts come from the API as plain text (no UI copies).
    for key in ("invalid_answer", "answer_unclear", "conf_possible", "outside_help"):
        assert key in TEXT


def test_evidence_pills_have_text():  # B-050
    from chat import evidence_label

    assert evidence_label("pwd_list_parallel") == "Other carriageway on the PWD list"
    assert evidence_label("road_ref") == "National highway (NH-48)"


def test_no_live_buttons_after_the_dossier(app):  # B-039
    from lib import fake_api

    submit(app, "gaddha #road_q")
    reply(app, "inner_lane")
    thread = app.session_state["thread"]  # a stale re-ask landed after the dossier
    fake_api.CONV[thread].append({"role": "assistant", "kind": "question", "retry": True,
                                  "text": "Is this on a main road or an inner lane?",
                                  "options": fake_api.QUESTIONS["#road_q"][2], "at": "x"})
    app.run()
    assert not [b for b in app.button if b.key.startswith("reply_")]
    assert not app.chat_input


def test_reopened_outside_delhi_thread_says_outside_delhi(app):  # B-045
    submit(app, "gaddha", pin=MUMBAI)
    app.session_state["sent"] = {}  # as if opened from Recent in another session
    app.run()
    page = page_text(app)
    assert TEXT["outside_title"] in page and TEXT["uncovered_title"] not in page


def test_reopened_rejected_thread_keeps_the_reason(app):  # B-045
    submit(app, "gaddha hai #junk_meme")
    app.session_state["sent"] = {}
    app.run()
    assert TEXT["retake_meme"] in page_text(app)


def test_non_mcd_dossier_without_a_call_link_does_not_crash(app):  # B-052
    from lib import fake_api

    submit(app, "taar gira hua hai #discom_q")
    reply(app, "brpl")
    fake_api.LAST["decided"] |= {"channels": [  # a contact row with no deep_link
        {"type": "helpline", "label": "BSES Rajdhani", "value": "19123", "deep_link": None}]}
    app.session_state["dossiers"] = {}
    app.run()
    assert not app.exception
    assert "Call 19123" in page_text(app)  # the number as text, no button
    assert "tel:19123" not in links(app).values()
    open_letter(app)  # the full dossier page too
    assert not app.exception and "Call 19123" in page_text(app)


def test_answer_error_shows_at_the_bottom(app):  # B-054
    submit(app, "gaddha #road_q")
    app.chat_input(key="answer_text").set_value("hmm #429").run()
    assert not app.exception
    [err] = app.error
    assert err.value == TEXT["rate_limited_answers"]
    # in the last chat bubble (where the user typed), not in the slot at the top
    assert app.chat_message[-1].error[0].value == TEXT["rate_limited_answers"]
