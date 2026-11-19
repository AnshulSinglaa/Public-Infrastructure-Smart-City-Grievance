"""Deploy mini-sprint UI: email + password login (AUTH_MODE=supabase), demo banner, demo spots."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import account
from lib.api import ApiError
from lib.fake_api import TOKEN
from location import DEMO_SPOTS
from strings import TEXT

APP = str(Path(__file__).resolve().parents[1] / "app.py")
# The widget's token in Cloudflare's test mode (AppTest can't run the real widget).
SOLVED = lambda **kw: "XXXX.DUMMY.TOKEN.XXXX"  # noqa: E731


@pytest.fixture
def new_app(monkeypatch):
    monkeypatch.setenv("FAKE_API", "1")
    monkeypatch.setenv("AUTH_MODE", "supabase")
    return AppTest.from_file(APP, default_timeout=30)


def errors(app) -> list[str]:
    return [e.value for e in app.error]


def test_password_sign_in(new_app, monkeypatch):
    new_app.run()
    assert TEXT["demo_banner"] in " ".join(m.value for m in new_app.markdown)
    new_app.text_input(key="login_email").input("priya@example.com")
    new_app.text_input(key="login_password").input("correct-horse")
    new_app.button(key="login_go").click().run()  # the widget has no token yet
    assert TEXT["need_captcha"] in errors(new_app)
    monkeypatch.setattr(account, "turnstile", SOLVED)
    new_app.button(key="login_go").click().run()
    assert new_app.session_state["token"] == TOKEN
    assert new_app.session_state["user_name"] == "Priya"


def test_empty_fields_are_caught_before_the_api(new_app, monkeypatch):
    monkeypatch.setattr(account, "turnstile", SOLVED)
    new_app.run()
    new_app.button(key="login_go").click().run()
    assert TEXT["need_email"] in errors(new_app)
    new_app.text_input(key="login_email").input("priya@example.com")
    new_app.button(key="login_go").click().run()
    assert TEXT["need_password"] in errors(new_app)
    assert "token" not in new_app.session_state


def test_wrong_password_keeps_the_error_and_swaps_the_widget(new_app, monkeypatch):
    """The Turnstile token is single-use: after a failed try the widget is replaced right
    away, so the next click is not met by 'wait for the security check'."""
    monkeypatch.setattr(account, "turnstile", SOLVED)
    new_app.run()
    new_app.text_input(key="login_email").input("priya@example.com")
    new_app.text_input(key="login_password").input("wrong-password")
    new_app.button(key="login_go").click().run()
    assert TEXT["bad_credentials"] in errors(new_app)
    assert new_app.session_state["ts_round"] == 1  # a fresh widget for the retry
    assert "token" not in new_app.session_state
    new_app.text_input(key="login_password").input("correct-horse")  # and the retry works
    new_app.button(key="login_go").click().run()
    assert new_app.session_state["token"] == TOKEN


def test_create_account(new_app, monkeypatch):
    monkeypatch.setattr(account, "turnstile", SOLVED)
    new_app.run()
    new_app.radio(key="auth_tab").set_value(TEXT["tab_create"]).run()
    new_app.text_input(key="login_email").input("priya@example.com")
    new_app.text_input(key="login_password").input("long-enough-1")
    new_app.button(key="login_go").click().run()
    assert TEXT["need_name"] in errors(new_app)
    new_app.text_input(key="login_name").input("Priya")
    new_app.text_input(key="login_password").input("short")
    new_app.button(key="login_go").click().run()
    assert TEXT["short_password"] in errors(new_app)
    new_app.text_input(key="login_password").input("long-enough-1")
    new_app.button(key="login_go").click().run()
    assert new_app.session_state["token"] == TOKEN
    assert new_app.session_state["user_name"] == "Priya"


def test_create_account_with_a_taken_email(new_app, monkeypatch):
    monkeypatch.setattr(account, "turnstile", SOLVED)
    new_app.run()
    new_app.radio(key="auth_tab").set_value(TEXT["tab_create"]).run()
    new_app.text_input(key="login_name").input("Priya")
    new_app.text_input(key="login_email").input("taken@example.com")
    new_app.text_input(key="login_password").input("long-enough-1")
    new_app.button(key="login_go").click().run()
    assert TEXT["email_taken"] in errors(new_app)
    assert "token" not in new_app.session_state


def test_an_api_outage_shows_a_friendly_message(new_app, monkeypatch):
    def down(*args, **kwargs):
        raise ApiError("api_unreachable")

    monkeypatch.setattr(account, "turnstile", SOLVED)
    monkeypatch.setattr(account, "sign_in", down)
    new_app.run()
    new_app.text_input(key="login_email").input("priya@example.com")
    new_app.text_input(key="login_password").input("correct-horse")
    new_app.button(key="login_go").click().run()
    assert TEXT["api_unreachable"] in errors(new_app)


def test_waking_screen_waits_for_a_sleeping_server(new_app, monkeypatch):
    """Render's free API sleeps; the first visit waits for it instead of failing."""
    import time

    import lib.api

    answers = iter([False, False, True])  # asleep, still booting, awake
    monkeypatch.setattr(lib.api, "health", lambda timeout=3: next(answers, True))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    new_app.run()
    assert new_app.session_state["api_awake"] is True
    assert new_app.text_input(key="login_email")  # the login shows once it's awake


def test_waking_screen_gives_up_with_a_retry(new_app, monkeypatch):
    import time

    import lib.api

    monkeypatch.setattr(lib.api, "health", lambda timeout=3: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    new_app.run()
    assert "api_awake" not in new_app.session_state
    assert new_app.button(key="wake_retry")
    assert not new_app.text_input  # no login form while the server is down


def test_demo_spot_sets_the_pin(new_app):
    new_app.session_state["token"] = TOKEN
    new_app.session_state["loc_open"] = True
    new_app.run()
    label = next(iter(DEMO_SPOTS))
    new_app.selectbox(key="demo_spot").select(label).run()
    pin = new_app.session_state["pin"]
    assert (pin["lat"], pin["lng"]) == DEMO_SPOTS[label] and pin["source"] == "landmark"
    assert new_app.selectbox(key="demo_spot").value is None  # ready to pick again
