"""Output guards (HLD §5.2, FIX-PLAN-S6 A): on the model's JSON, before anything is shown.
Plain code.

The model returns a plan (QAPlan): responsible body -> where to complain -> steps ->
escalation -> notes, each a sentence with its cites. flatten() turns it into sentences with
a `part`, in that order, at most MAX_SENTENCES.

Per sentence (a failing sentence is dropped, never fixed):
  1 every cite id is one we sent        2 cite-or-drop: >= 1 quote really in its source
  3 every number is in a verified quote, and numbers only when the user asked for one
  4 no other department (outside a quote; /ask only)
  5 no channel that doesn't apply to this issue (CPGRAMS for a town drain, B-065)
  6 no PII (mask_pii must not change it)
Whole answer: 7 a small blocklist -> not_found; 8 nothing left -> not_found.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.intake.catalog import departments
from app.privacy.text import mask_pii
from app.rag.verify import numbers_ok, quote_ok


class Cite(BaseModel):
    id: str = Field(max_length=8)
    quote: str = Field(max_length=600)


class Sentence(BaseModel):
    text: str = Field(max_length=600)
    cites: list[Cite] = Field(default=[], max_length=6)


class QAPlan(BaseModel):
    status: Literal["answered", "not_found", "out_of_scope"] = "not_found"
    responsible: Sentence | None = None
    where: Sentence | None = None
    steps: list[Sentence] = Field(default=[], max_length=8)
    escalation: Sentence | None = None
    notes: list[Sentence] = Field(default=[], max_length=4)


NOT_FOUND = {"status": "not_found", "responsible": None, "where": None, "steps": [],
             "escalation": None, "notes": []}
MAX_SENTENCES, MAX_STEPS = 6, 4

BLOCKLIST = re.compile(
    r"\b(kill|murder|rape|bomb|terrorist|idiot|stupid|bastard|bitch|chutiya|madarchod|"
    r"behenchod|bhenchod|harami|kamina|randi|saala)\b", re.IGNORECASE)
CHANNELS = {"CPGRAMS", "PGMS", "DELHI_311"}  # where-to-ask channels, not departments
CENTRAL_CHANNEL = re.compile(r"cpgrams|pgportal|pg portal|darpg", re.IGNORECASE)
ASKS_NUMBER = re.compile(
    r"\d|how (many|much|long|soon)|kitn[aie]|kab tak|\bdays?\b|\bdin\b|\bfees?\b|\bcost\b|"
    r"deadline|time limit|within|helpline|number|कितन|दिन|शुल्क", re.IGNORECASE)


def flatten(plan: dict) -> list[dict]:
    """The plan's sentences in reading order, each with its `part`."""
    out = []
    for part, items in (("responsible", [plan.get("responsible")]),
                        ("where", [plan.get("where")]),
                        ("step", (plan.get("steps") or [])[:MAX_STEPS]),
                        ("escalation", [plan.get("escalation")]),
                        ("note", (plan.get("notes") or [])[:1])):
        out += [s | {"part": part} for s in items if s]
    return out[:MAX_SENTENCES]


def named_departments(text: str) -> set[str]:
    """Department codes named in text: the code as a word (PWD), or its English name."""
    found = set()
    for code, row in departments().items():
        if code in CHANNELS:
            continue
        if re.search(rf"(?<![A-Za-z_]){re.escape(code)}(?![A-Za-z_])", text) \
                or (row["name_en"] and row["name_en"].casefold() in text.casefold()):
            found.add(code)
    return found


def check_sentence(s: dict, sources: dict[str, str], allowed: set[str] | None,
                   numbers_wanted: bool, central_ok: bool):
    """(kept sentence {text, part, cites: [(id, quote)]}, None) or (None, drop reason)."""
    text = s["text"].strip()
    if not text or not s["cites"]:
        return None, "no_cite"
    if any(c["id"] not in sources for c in s["cites"]):
        return None, "unknown_source"
    verified = [(c["id"], c["quote"].strip()) for c in s["cites"]
                if quote_ok(c["quote"], sources[c["id"]])]
    if not verified:
        return None, "quote_not_in_source"
    if not numbers_ok(text, [q for _, q in verified]):
        return None, "number"
    if not numbers_wanted and any(ch.isdigit() for ch in text):
        return None, "number_not_asked"  # no statistics unless the user asked (B-063)
    if allowed is not None:
        outside = text
        for _, q in verified:  # a department named inside a verified quote is allowed
            outside = re.sub(re.escape(q), " ", outside, flags=re.IGNORECASE)
        if named_departments(outside) - allowed:
            return None, "department"
    if not central_ok and CENTRAL_CHANNEL.search(text):
        return None, "channel_not_applicable"
    if mask_pii(text) != text:
        return None, "pii"
    return {"text": text, "part": s.get("part", "note"), "cites": verified}, None


def check(plan: dict, sources: dict[str, str], allowed: set[str] | None, *,
          question: str = "", central_ok: bool = True) -> dict:
    """plan = QAPlan as a dict; sources = id -> its full text (Y facts and C chunks);
    allowed = department codes a sentence may name (None = no department check, /chat);
    central_ok = False for a state/local issue (no CPGRAMS). Returns
    {"status", "sentences": [{text, part, cites: [(id, quote)]}], "dropped": [reasons]}."""
    if plan["status"] != "answered":
        return {"status": plan["status"], "sentences": [], "dropped": []}
    wanted = bool(ASKS_NUMBER.search(question))
    kept, dropped = [], []
    for s in flatten(plan):
        ok, reason = check_sentence(s, sources, allowed, wanted, central_ok)
        if ok:
            kept.append(ok)
        else:
            dropped.append(reason)
    if any(BLOCKLIST.search(s["text"]) for s in kept):
        return {"status": "not_found", "sentences": [], "dropped": dropped + ["safety"]}
    return {"status": "answered" if kept else "not_found", "sentences": kept,
            "dropped": dropped}
