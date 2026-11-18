"""Self-check for the scorer (no DB, no network): uv run pytest eval -q"""

import run_eval as ev

BADHRA = next(r for r in ev.load_golden() if r["id"] == "up-01")


def rec(body, **extra):
    return {"id": "up-01", "http": 200, "latency_s": 4.0, "body": body,
            "usage": [{"task": "qa_answer", "provider": "groq", "input": 2500, "output": 400}],
            "quote_in_chunk": {"C1": True}, "dropped": [], **extra}


def body(text, quote, where, intent="general_civic", actions=()):
    return {"status": "answered", "intent": intent, "layer": "state", "local_rule_unknown": True,
            "sentences": [{"text": text, "cites": ["C1"]}],
            "sources": [{"id": "C1", "kind": "document", "quote": quote}],
            "where_to_ask": [{"label": w, "value": w} for w in where],
            "actions": [{"kind": a} for a in actions]}


def test_the_users_badhra_answer_fails_the_upgrade_checks():
    bad = body("आप अपनी समस्या CM Window में दर्ज करा सकते हैं। कुल 1,624,414 शिकायतें प्राप्त हुई हैं।",
               "CM WINDOW काउंटर पर", ["CM Window (CM Grievance Cell)", "CPGRAMS"])
    c = ev.score_row(BADHRA, rec(bad), "live")
    assert c["language"] is False and c["channel"] is False and c["actions"] is False
    assert c["no_irrelevant_numbers"] is False and c["numbers_in_shown_quotes"] is False
    assert c["authority"] is False


def test_a_good_badhra_answer_passes():
    good = body("Waterlogging in a town is usually handled by the Municipal Committee; you can "
                "complain on CM Window.", "complaint on CM Window", ["CM Window (CM Grievance Cell)"],
                actions=["start_report"])
    c = ev.score_row(BADHRA, rec(good), "live")
    assert all(v is not False for v in c.values()), c


def test_summary_gate_and_languages():
    assert ev.lang_of("Aap CM Window par shikayat kar sakte hain, yeh kaam ke liye hai") == "hinglish"
    assert ev.lang_of("Complain on the CM Window portal.") == "en"
    assert ev.lang_of("सीएम विंडो पर शिकायत करें") == "hi"
    report, gate = ev.summarize([BADHRA], {"up-01": rec(body(
        "Use CPGRAMS.", "x", ["CPGRAMS"]))}, {"mode": "live"})
    assert not gate and "Right channel" in report and "up-01" in report
