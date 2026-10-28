import re
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from i18n import TEXT  # `ui` is on pytest's pythonpath (pyproject.toml)
from lib.api import ApiError, _check

UI_DIR = Path(__file__).resolve().parents[1]
BACKEND_APP = UI_DIR.parent / "backend" / "app"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("FAKE_API", "1")  # canned backend, see ui/lib/fake_api.py
    at = AppTest.from_file(str(UI_DIR / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    return at


def test_empty_submit_shows_error(app):
    app.button(key="prepare").click().run()
    assert [e.value for e in app.error] == [TEXT["hi"]["need_photo_or_text"]]
    assert "dossier" not in app.session_state


def test_text_submit_shows_dossier(app):
    app.text_area(key="report_text").input("Bada gaddha hai school ke saamne").run()
    app.button(key="prepare").click().run()
    assert not app.exception
    assert not app.error
    page = " ".join(m.value for m in app.markdown)
    assert "दिल्ली नगर निगम" in page  # department in the chosen language (Hindi default)
    assert "जाँचा हुआ डेटा" in page and "15 दिन" in page
    assert "AI" in page  # fake-AI notice
    assert len(app.code) == 2  # HI + EN copy boxes


def test_both_languages_have_same_keys():
    assert TEXT["hi"].keys() == TEXT["en"].keys()


def test_every_api_error_key_has_text():
    # Every message_key the backend can send, read from its source, so a new key fails here.
    src = " ".join(f.read_text(encoding="utf-8") for f in BACKEND_APP.rglob("*.py"))
    keys = set(re.findall(r'error\(\d+, "\w+", "(\w+)"\)|"message_key": "(\w+)"', src))
    keys = {k for pair in keys for k in pair if k}
    assert "photo_not_jpeg_png" in keys and "not_found" in keys  # the regex still works
    assert keys <= TEXT["hi"].keys()


def test_unknown_error_key_falls_back_to_generic():
    with pytest.raises(ApiError) as e:
        _check(httpx.Response(422, json={"error": {"message_key": "brand_new_key"}}))
    assert e.value.message_key == "server_error"


def test_complaint_tab_follows_ui_language(app):
    app.sidebar.segmented_control[0].set_value("English").run()
    app.text_area(key="report_text").input("Big pothole").run()
    app.button(key="prepare").click().run()
    assert [tab.label for tab in app.tabs] == ["English", "हिंदी"]


def test_out_of_coverage_shows_helplines_not_a_department(app):
    app.session_state["uncovered"] = [
        {"type": "helpline", "label": "MCD 311", "value": "155305", "deep_link": "tel:155305"}
    ]
    app.run()
    page = " ".join(m.value for m in app.markdown)
    assert TEXT["hi"]["uncovered_title"] in page
    assert TEXT["hi"]["who_fixes"] not in page  # no department claim


def test_language_switch_keeps_gallery_photo(app):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    app.segmented_control(key="photo_source").set_value("gallery").run()
    app.file_uploader[0].upload("p.png", png, "image/png").run()
    app.sidebar.segmented_control[0].set_value("English").run()  # every label changes language
    app.button(key="prepare").click().run()
    assert not app.error
    assert app.session_state["photo"] == png  # the photo was still there and got sent


def test_uncovered_report_shows_helpline_button(app):
    app.text_area(key="report_text").input("gaddha, uncovered area").run()
    app.button(key="prepare").click().run()
    assert not app.exception and not app.error
    assert app.session_state["uncovered"][0]["value"] == "155305"
    assert "dossier" not in app.session_state  # no department claim
    [call] = app.get("link_button")
    assert call.proto.url == "tel:155305" and "155305" in call.proto.label
