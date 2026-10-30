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
    assert new_app.session_state["view"] == "dossier"


def test_second_gps_fix_that_drifts_stops_and_warns(new_app, monkeypatch):
    fake_gps(monkeypatch, {"open": (28.5440, 77.311, 10), "submit": (28.5450, 77.311, 10)})
    new_app.session_state["token"] = TOKEN
    new_app.run()
    new_app.text_area(key="report_text").input("gaddha").run()
    new_app.button(key="prepare").click().run()
    assert new_app.session_state.get("view", "new") == "new"  # not sent: ~111 m apart
    assert TEXT["loc_shaky"] in page_text(new_app)
    new_app.button(key="prepare").click().run()  # user checked the pin; second click sends
    assert new_app.session_state["view"] == "dossier"


def test_text_submit_shows_dossier(app):
    submit(app, "Bada gaddha hai school ke saamne")
    assert not app.error
    page = page_text(app)
    assert "Municipal Corporation of Delhi" in page
    assert "Curated data" in page and "15 days" in page and "Very likely" in page
    assert "Bada gaddha hai school ke saamne" in page  # citizen text, exactly as written
    assert "AI is simulated" in page
    [letter] = app.code
    assert letter.value.startswith("To, Municipal")  # English letter only


def test_dossier_shows_blurred_photo_not_raw_upload(app, monkeypatch):
    import report

    calls = []
    monkeypatch.setattr(report, "get_photo", lambda tok, url: calls.append(url) or BLURRED)
    upload_photo(app)
    submit(app, "kooda")
    assert calls == ["/api/v1/media/00000000-0000-0000-0000-00000000d055"]
    assert app.session_state["current"]["photo"] == BLURRED
    assert "Photo location not verified" in page_text(app)  # uploaded, not camera


def test_withheld_photo_says_so(app):
    upload_photo(app)
    submit(app, "kooda #withheld")
    assert TEXT["photo_withheld"] in [c.value for c in app.caption]


def test_remove_photo(app):
    upload_photo(app)
    app.button(key="remove_photo").click().run()
    assert "photo_draft" not in app.session_state


def test_rate_limit_shows_message(app):
    submit(app, "gaddha #429")
    assert [e.value for e in app.error] == [TEXT["rate_limited_daily"]]


def test_my_reports_lists_reports_and_opens_dossier(app):
    app.button(key="nav_reports").click().run()
    page = page_text(app)
    assert "Pothole · Ward 185" in page and "MCD" in page and "2026-10-26" in page
    app.button(key="open_00000000-0000-0000-0000-0000000000a0").click().run()
    assert not app.exception
    assert app.session_state["view"] == "dossier"


def test_sidebar_recent_opens_dossier(app):
    recent = [b for b in app.sidebar.button if b.key.startswith("recent_")]
    assert 1 <= len(recent) <= 5
    recent[-1].click().run()
    assert app.session_state["view"] == "dossier"


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
    caption = " ".join(c.value for c in app.caption)
    assert TEXT["outside_title"] in page_text(app) and "Maharashtra" in caption
    assert "anywhere in Delhi" not in caption  # no Delhi helplines outside Delhi
    assert links(app) == {"Aaple Sarkar": "https://grievances.maharashtra.gov.in",
                          "Call 1800120800": "tel:1800120800",
                          "CPGRAMS": "https://pgportal.gov.in"}


def test_unverified_state_shows_cpgrams_only(app):
    submit(app, "gaddha #unverified", pin=MUMBAI)
    assert links(app) == {"CPGRAMS": "https://pgportal.gov.in"}
    assert TEXT["state_unknown_help"] in [c.value for c in app.caption]


def test_clarify_flow_reaches_dossier(app):
    submit(app, "kuch toh hai #clarify")
    assert TEXT["clarify_q"] in page_text(app)
    assert "report_text" not in [w.key for w in app.text_area]  # the card replaces the composer
    assert app.button(key="opt_POTHOLE").label == "Pothole · likely"  # name_en from the API
    assert app.button(key="opt_WATERLOGGING").label == "Waterlogging · maybe"
    app.button(key="opt_POTHOLE").click().run()
    assert not app.exception and app.session_state["view"] == "dossier"
    assert "clarify" not in app.session_state


def test_clarify_something_else_uses_the_select(app):
    submit(app, "kuch toh hai #clarify")
    app.button(key="opt_other").click().run()
    select = app.selectbox(key="other_category")
    assert "Dead animal" in select.options  # read from GET /categories, not hard-coded
    select.set_value("DEAD_ANIMAL").run()
    app.button(key="use_other").click().run()
    assert app.session_state["view"] == "dossier"


def test_mismatch_question_names_both(app):
    submit(app, "light kharab hai #mismatch")
    assert ("Your photo looks like garbage dump, your text says streetlight not working"
            in page_text(app))
    app.button(key="opt_STREETLIGHT_OUT").click().run()
    assert app.session_state["view"] == "dossier"


def test_retake_keeps_text_and_opens_camera(app):
    submit(app, "gaddha hai #junk_selfie")
    assert TEXT["retake_selfie"] in " ".join(c.value for c in app.caption)
    app.button(key="retake_btn").click().run()
    assert app.session_state["report_text"] == "gaddha hai #junk_selfie"
    assert app.session_state["attach"] == "camera" and "retake" not in app.session_state


def test_emergency_card_sits_above_the_dossier(app):
    submit(app, "taar gira hua hai #emergency")
    assert app.session_state["view"] == "dossier"
    page = page_text(app)
    assert page.index(TEXT["emergency_call_first"]) < page.index(TEXT["dossier_title"])
    assert list(links(app).values())[:2] == ["tel:112", "tel:19123"]  # call-first come first


def test_ai_unavailable_keeps_the_draft(app):
    submit(app, "gaddha #503")
    assert [e.value for e in app.error] == [TEXT["ai_unavailable"]]
    assert app.session_state["report_text"] == "gaddha #503"
    assert app.session_state.get("view", "new") == "new"


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
