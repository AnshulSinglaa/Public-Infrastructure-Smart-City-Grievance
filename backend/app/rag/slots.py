"""What we know about the user's problem, kept per chat thread (FIX-PLAN-S6 D, E, F, B, C).
All plain code, no model:

- slots: state, town (its local body), issue type, "it's my own problem", and the issue's scope.
  Filled from each message; a follow-up ("how do I report this?") keeps the last ones.
- scope (applicability): rti | national_highways | central_ministry_matters |
  local_body_matters. Matches sources.yaml `covers` / `not_for`, so CPGRAMS (central) is
  never offered for a town drain.
- language: en | hinglish | hi, from the question's script and words (the answer must match).
- local body: town name -> the LGD local body (table local_body, migration 0010).
"""

import re
from itertools import pairwise

from app.privacy.text import WORDCHAR

DEVANAGARI = re.compile("[ऀ-ॿ]")
ISSUES = {  # issue type -> words (EN / Hinglish / Hindi)
    "waterlogging": ["waterlogging", "water logging", "water clogging", "waterlogged",
                     "clogging", "paani bhar", "pani bhar", "jal bharav", "जलभराव", "पानी भर"],
    "drain": ["drain", "nala", "naali", "nali", "gutter", "sewer", "sewage", "manhole",
              "नाली", "नाला", "सीवर"],
    "garbage": ["garbage", "trash", "waste", "kachra", "kachara", "kooda", "kuda", "कचरा",
                "कूड़ा"],
    "streetlight": ["streetlight", "street light", "street lights", "streetlights",
                    "light pole", "बत्ती"],
    "road": ["pothole", "potholes", "gaddha", "gadda", "road", "sadak", "footpath", "गड्ढा",
             "सड़क"],
    "water_supply": ["water supply", "no water", "paani nahi", "pani nahi", "tap water",
                     "पानी नहीं"],
    "power": ["electricity", "power cut", "bijli", "live wire", "बिजली"],
}
SCOPES = {
    "rti": r"\brti\b|right to information|आरटीआई|सूचना का अधिकार",
    "national_highways": r"\bnh[\s-]?\d+|national highway|\bnhai\b|expressway|toll plaza",
    "central_ministry_matters": r"passport|railway|\btrain\b|post office|income tax|\bepfo\b|"
                                r"\bpension\b|central (ministry|government)|\bcghs\b|aadhaar card",
}
HINGLISH = {"hai", "hain", "kya", "kaise", "kahan", "kahaan", "mein", "ka", "ki", "ke", "nahi",
            "karein", "kare", "karu", "kaun", "mera", "meri", "mere", "ho", "raha", "rahi", "kab",
            "kyun", "karna", "batao", "bataiye", "aur", "ko", "se", "par", "paas", "wala"}
OWN = re.compile(r"\b(my|our|mere|meri|mera|hamare|hamari|humare)\b.{0,40}\b(house|home|"
                 r"street|gali|colony|ghar|lane|area|mohalla|society|building)\b|in front of my|"
                 r"near my|outside my|मेरे घर|हमारी गली", re.IGNORECASE)


def words(text: str) -> list[str]:
    return re.findall(rf"{WORDCHAR}+", text.casefold())


def devanagari_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(bool(DEVANAGARI.match(c)) for c in letters) / len(letters) if letters else 0.0


def language(text: str) -> str:
    """hi (Devanagari) | hinglish (Hindi in Latin letters) | en."""
    if devanagari_ratio(text) > 0.3:
        return "hi"
    return "hinglish" if len(set(words(text)) & HINGLISH) >= 2 else "en"


def issue_type(text: str) -> str | None:
    low = text.casefold()
    for issue, cues in ISSUES.items():
        if any(re.search(rf"(?<!{WORDCHAR}){re.escape(c)}(?!{WORDCHAR})", low) for c in cues):
            return issue
    return None


def scope_of(text: str) -> str | None:
    """The scope named in this message, or None (then the thread's, else a local matter)."""
    return next((name for name, pattern in SCOPES.items()
                 if re.search(pattern, text, re.IGNORECASE)), None)


def town_in(conn, text: str, state_code: str | None) -> dict | None:
    """The LGD local body named in the text: in the given state, or else a single match of a
    long-enough name anywhere in India (also gives the state)."""
    w = words(text)
    names = list({*w, *(" ".join(p) for p in pairwise(w))})
    try:
        rows = conn.execute(
            "SELECT name, body_type, district, state_code FROM local_body"
            " WHERE name_norm = ANY(%s) AND (%s::text IS NULL OR state_code = %s)"
            " ORDER BY length(name_norm) DESC LIMIT 3",
            (names, state_code, state_code)).fetchall()
    except Exception:  # noqa: BLE001 - local_body not loaded yet: no town, not an error
        conn.rollback()
        return None
    if not rows or (state_code is None and (len(rows) > 1 or len(rows[0][0]) < 5)):
        return None
    name, body_type, district, code = rows[0]
    return {"name": name, "body_type": body_type, "district": district, "state_code": code,
            "label": f"{body_type} {name}"}


def extract(conn, text: str, state_code: str | None, previous: dict | None) -> dict:
    """This message's slots, with the last turn's carried over for a follow-up. A message
    that names a different scope (RTI, a national highway) doesn't inherit the place."""
    previous = previous or {}
    scope = scope_of(text)
    follow_up = scope is None or scope == previous.get("scope")
    carry = previous if follow_up else {}
    town = town_in(conn, text, state_code) or (
        carry.get("town") if not state_code or state_code == (carry.get("town") or {}).get(
            "state_code") else None)
    return {"state": state_code or (town or {}).get("state_code") or carry.get("state"),
            "town": town,
            "issue_type": issue_type(text) or carry.get("issue_type"),
            "own_problem": bool(OWN.search(text)) or bool(carry.get("own_problem")),
            "scope": scope or carry.get("scope") or "local_body_matters"}
