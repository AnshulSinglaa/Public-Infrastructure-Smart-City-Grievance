"""Slot memory: what a follow-up keeps and what a new question must not inherit."""

from app.rag import slots


def no_town(monkeypatch):
    monkeypatch.setattr(slots, "town_in", lambda conn, text, state: None)


def test_new_local_issue_does_not_inherit_a_national_scope(monkeypatch):
    no_town(monkeypatch)
    previous = {"scope": "rti", "state": "IN", "issue_type": None}
    got = slots.extract(None, "Gurugram garbage complaint kahan karein?", "HR", previous)
    assert got["scope"] == "local_body_matters" and got["issue_type"] == "garbage"


def test_follow_up_without_an_issue_keeps_the_national_scope(monkeypatch):
    no_town(monkeypatch)
    previous = {"scope": "rti", "state": "IN", "issue_type": None}
    got = slots.extract(None, "and how many days does it take?", "IN", previous)
    assert got["scope"] == "rti"


def test_follow_up_keeps_the_local_issue_and_place(monkeypatch):
    no_town(monkeypatch)
    previous = {"scope": "local_body_matters", "state": "HR", "issue_type": "waterlogging",
                "own_problem": True}
    got = slots.extract(None, "can u tell me step by step how to report this issue", "HR",
                        previous)
    assert got["scope"] == "local_body_matters" and got["issue_type"] == "waterlogging"
    assert got["own_problem"] is True and got["state"] == "HR"
