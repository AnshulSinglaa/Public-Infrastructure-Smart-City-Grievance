import io
import re
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


def test_outside_delhi_gets_its_own_card(app):  # B-018
    submit(app, "gaddha", pin={**PIN, "lat": 19.07, "lng": 72.87})  # Mumbai
    caption = " ".join(c.value for c in app.caption)
    assert TEXT["outside_title"] in page_text(app) and "CPGRAMS" in caption
    assert "anywhere in Delhi" not in caption
    [portal] = app.get("link_button")  # no Delhi helpline, only the national portal
    assert portal.proto.url == "https://pgportal.gov.in"


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
