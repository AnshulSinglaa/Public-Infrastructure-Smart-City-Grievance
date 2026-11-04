"""Deploy mini-sprint UI: email-code login (AUTH_MODE=supabase), demo banner, demo spots."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import account
from lib.fake_api import TOKEN
from location import DEMO_SPOTS
from strings import TEXT

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def new_app(monkeypatch):
    monkeypatch.setenv("FAKE_API", "1")
    return AppTest.from_file(APP, default_timeout=30)


def test_email_code_login(new_app, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "supabase")
    new_app.run()
    assert TEXT["demo_banner"] in " ".join(m.value for m in new_app.markdown)
    new_app.text_input(key="login_name").input("Priya")
    new_app.text_input(key="login_email").input("priya@example.com")
    new_app.button(key="login_go").click().run()  # the widget has no token yet
    assert TEXT["need_captcha"] in [e.value for e in new_app.error]
    # The widget's token in Cloudflare's test mode (AppTest can't run the real widget).
    monkeypatch.setattr(account, "turnstile", lambda **kw: "XXXX.DUMMY.TOKEN.XXXX")
    new_app.button(key="login_go").click().run()
    assert "token" not in new_app.session_state
    new_app.text_input(key="login_code").input("000000")
    new_app.button(key="code_go").click().run()
    assert TEXT["bad_code"] in [e.value for e in new_app.error]
    new_app.text_input(key="login_code").input("123456")
    new_app.button(key="code_go").click().run()
    assert new_app.session_state["token"] == TOKEN
    assert new_app.session_state["user_name"] == "Priya"


def test_demo_spot_sets_the_pin(new_app):
    new_app.session_state["token"] = TOKEN
    new_app.session_state["loc_open"] = True
    new_app.run()
    label = next(iter(DEMO_SPOTS))
    new_app.selectbox(key="demo_spot").select(label).run()
    pin = new_app.session_state["pin"]
    assert (pin["lat"], pin["lng"]) == DEMO_SPOTS[label] and pin["source"] == "landmark"
    assert new_app.selectbox(key="demo_spot").value is None  # ready to pick again
