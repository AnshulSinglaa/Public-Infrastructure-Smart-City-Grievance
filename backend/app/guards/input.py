"""Input guards (HLD §5.1), in this order, before any big-model call:

  1 format (1-300 chars, plain text, not only links/emoji)    -> 422 invalid_question
  2 mask_pii (app/privacy/text.py), done by the caller
  3 injection rules (regex)                                   -> blocked, 0 tokens
  4 intent gate: rules -> embedding similarity to the example bank (data/intent_examples.csv)
    -> the small model (Groq 8B) only when the similarity is borderline.
    greeting / off_domain / new_problem / my_complaint / general_civic

Rules win when they fire. No embedding -> the small model decides; small model down too ->
general_civic (it is still grounded, so the worst case is "I don't know...").
"""

import csv
import hashlib
import json
import logging
import re
import time
from functools import cache
from typing import Literal

from pydantic import BaseModel

from app.config import ROOT, settings
from app.intake.catalog import departments
from app.llm import gateway
from app.privacy.text import WORDCHAR
from app.providers.common import ProviderDown, load_prompt

log = logging.getLogger(__name__)
MAX_CHARS = 300
LABELS = ("greeting", "off_domain", "new_problem", "my_complaint", "general_civic")

# ---------- 1. format ----------

URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def clean_question(text: str | None) -> str | None:
    """The trimmed question, or None when it must be rejected (422)."""
    text = (text or "").strip()
    if not 1 <= len(text) <= MAX_CHARS:
        return None
    if any(ord(c) < 32 and c not in "\t\n" for c in text):  # control characters: not plain text
        return None
    if not any(c.isalpha() for c in URL.sub("", text)):  # only links, digits or emoji
        return None
    return text


# ---------- 3. injection ----------

INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|your)\b"
    r".{0,20}\b(instructions?|rules?|prompts?|directions?)\b"
    r"|system\s*prompt|developer\s*mode|jailbreak|\byou are now\b|\bact as (an? )?(ai|llm|"
    r"assistant|system|admin|developer)\b|\bnew instructions?\b"
    r"|</?\s*(system|assistant|user|doc|question|fact|history|im_start|im_end)\b|\[/?inst\]"
    r"|pichl[ei] (saar[ei] )?(nirdesh|instructions?)|niyam bhool"
    r"|\b(print|show|repeat|reveal|tell me)\b.{0,30}\b(your|the)\b.{0,15}\b(instructions?|prompt|"
    r"rules)\b|\brepeat the (text|words) above\b"
    r"|[A-Za-z0-9+/]{80,}={0,2}",  # a long base64 blob
    re.IGNORECASE | re.DOTALL)


def is_injection(text: str) -> bool:
    return bool(INJECTION.search(text))


# ---------- 4a. intent rules ----------

def words(text: str) -> list[str]:
    return re.findall(rf"{WORDCHAR}+", text.casefold())


GREETING_WORDS = {
    "hi", "hii", "hello", "hey", "namaste", "namaskar", "thanks", "thank", "you", "thx",
    "shukriya", "dhanyavad", "dhanyawad", "ok", "okay", "good", "morning", "evening",
    "afternoon", "night", "bye", "ji", "sir", "madam", "there", "so", "much", "very",
    "नमस्ते", "नमस्कार", "धन्यवाद", "शुक्रिया"}
GREETING_PHRASES = re.compile(
    r"^(what can you do|who are you|aap kaun ho|tum kaun ho|what do you do|how are you|"
    r"kaise ho|aap kya kar sakte ho|help|madad)\??$")
DENY = re.compile(
    r"\b(poem|poetry|kavita|shayari|joke|chutkula|cricket|ipl|match score|football|stock|"
    r"shares?|sensex|bitcoin|crypto|recipe|cook|weather|mausam|movie|film|song|gaana|lyrics|"
    r"homework|essay|python|javascript|java|coding|write (me )?(some )?code|program(ming)?|"
    r"horoscope|rashifal|girlfriend|boyfriend|dating|election|vote for|bill kam|translate|"
    r"capital of)\b")
MY = re.compile(
    r"\b(my|meri|mera|mere|hamari|hamara)\s+(\w+\s+)?(complaint|report|shikayat|grievance|"
    r"case|application)\b|मेरी शिकायत|मेरा शिकायत|\bstatus of my\b")  # "my pothole report" too
WHY = {"why", "kyun", "kyon", "kyu", "क्यों"}
QUESTION_WORDS = {
    "who", "what", "how", "where", "why", "which", "when", "whom", "whose", "kaun", "kon", "kya", "kaise", "kese", "kahan", "kahaan", "kidhar",
    "kyun", "kyon", "kyu", "kab", "kitne", "kitna", "kitni", "kis", "kisko", "kisse", "konsa",
    "kaunsa", "कौन", "क्या", "कैसे", "कहाँ", "कहां", "क्यों", "कब", "कितने", "किस"}
PROBLEM_WORDS = {
    "pothole", "potholes", "gaddha", "gadda", "gaddhe", "kachra", "kachara", "kooda", "kuda",
    "garbage", "trash", "broken", "toota", "tuta", "tooti", "tuti", "leak", "leaking",
    "overflow", "overflowing", "blocked", "jam", "kharab", "fallen", "gira", "giri", "dead",
    "stray", "open", "flooded", "waterlogging", "waterlogged", "clogging", "clogged",
    "logging", "smell", "badbu", "गड्ढा", "कचरा", "टूटी",
    "टूटा", "बंद", "खराब", "गंदगी"}
PLACE_WORDS = {
    "near", "outside", "front", "opposite", "behind", "my", "our", "house", "home", "street",
    "gali", "colony", "mohalla", "paas", "pass", "saamne", "samne", "bahar", "yahan", "ghar",
    "mere", "hamare", "hamari", "humare", "society", "lane", "घर", "पास", "सामने", "गली"}
CIVIC_WORDS = {
    "complaint", "complain", "grievance", "rti", "road", "roads", "pothole", "garbage",
    "streetlight", "streetlights", "light", "sewer", "sewage", "drain", "nala", "naali", "nali",
    "water", "supply", "department", "municipal", "corporation", "ward", "highway", "highways",
    "portal", "helpline", "shikayat", "sadak", "kachra", "kooda", "gaddha", "fix", "fixes",
    "repair", "maintain", "maintains", "appeal", "fee", "cpgrams", "pgms", "deadline", "days",
    "manhole", "tree", "park", "toilet", "electricity", "file", "सड़क", "शिकायत", "नाली",
    "पानी", "बिजली", "report", "issue", "problem", "concern", "raise"}
CIVIC_WORDS |= {code.casefold() for code in departments()}


def rule_intent(text: str) -> str | None:
    w = words(text)
    flat = " ".join(w)
    if w and (len(w) <= 6 and set(w) <= GREETING_WORDS or GREETING_PHRASES.match(flat)):
        return "greeting"
    # "my pothole report", "why PWD?", "PWD kyun, MCD kyun nahi?" (B-067)
    if MY.search(flat) or (set(w) & WHY and any(x.upper() in departments() for x in w)):
        return "my_complaint"
    ws = set(w)
    ignore = {"file", "days", "fix", "light", "electricity", "issue", "problem", "concern"}
    if DENY.search(flat) and not ws & (CIVIC_WORDS - ignore):
        return "off_domain"
    asks = bool(ws & QUESTION_WORDS) or "?" in text or w[:1] in (["is"], ["can"], ["do"],
                                                                 ["does"], ["should"], ["are"])
    if ws & PROBLEM_WORDS and ws & PLACE_WORDS and not asks:
        return "new_problem"
    if ws & CIVIC_WORDS and asks:
        return "general_civic"
    return None


# ---------- 4b. example-bank similarity ----------

def bank() -> list[tuple[str, str]]:
    with (ROOT / "data" / "intent_examples.csv").open(encoding="utf-8", newline="") as f:
        return [(r["label"], r["text"]) for r in csv.DictReader(f)]


bank_failed_at = [0.0]  # when embedding the bank last failed (REVIEW B-05)


@cache
def bank_vectors() -> list[tuple[str, list[float]]]:
    """Embedded once per process; live vectors are also kept in .cache/llm (one Gemini call
    per bank version, not per restart). After a failure we don't resend the whole bank for
    60 s (that spends the quota we are short of)."""
    if time.monotonic() - bank_failed_at[0] < 60:
        raise ProviderDown("intent bank: embedding failed recently")
    rows = bank()
    texts = [t for _, t in rows]
    key = hashlib.sha256(json.dumps([settings.embed_model, settings.provider_mode, texts])
                         .encode()).hexdigest()
    path = settings.llm_cache_dir / f"intent_bank_{key}.json"
    if path.exists():
        vecs = json.loads(path.read_text(encoding="utf-8"))
    else:
        try:
            vecs = gateway.embed(texts, "RETRIEVAL_QUERY")
        except ProviderDown:
            bank_failed_at[0] = time.monotonic()
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vecs), encoding="utf-8")
    return [(label, v) for (label, _), v in zip(rows, vecs)]


def similarity_intent(text: str) -> tuple[str | None, dict]:
    """(label or None when borderline, best score per label)."""
    q = gateway.embed_query(text)
    best: dict[str, float] = {}
    for label, v in bank_vectors():
        best[label] = max(best.get(label, -1.0), sum(a * b for a, b in zip(q, v)))
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    (top, score), runner_up = ranked[0], (ranked[1][1] if len(ranked) > 1 else -1.0)
    if score < settings.intent_lo:
        return "off_domain", best
    if score >= settings.intent_hi and score - runner_up >= settings.intent_margin:
        return top, best
    return None, best


# ---------- 4c. the small model, borderline only ----------

class IntentChoice(BaseModel):
    label: Literal[LABELS] = "general_civic"


def model_intent(text: str, trace_seed: str | None) -> str:
    version, system = load_prompt("intent")
    user = "<question>\n" + re.sub(r"</?\s*question\s*>", "", text) + "\n</question>"
    out, _ = gateway.complete("intent", IntentChoice, system, user, prompt_version=version,
                              report_id=trace_seed, trace_input=text, timeout=5,
                              default={"label": "general_civic"})
    return out["label"]


def intent(text: str, trace_seed: str | None = None) -> tuple[str, str]:
    """(label, how) with how = rules | similarity | classifier | fallback. MASKED text only."""
    label = rule_intent(text)
    if label:
        return label, "rules"
    try:
        label, _ = similarity_intent(text)
        if label:
            return label, "similarity"
    except ProviderDown:
        log.warning("intent: no embedding, asking the small model")
    try:
        return model_intent(text, trace_seed), "classifier"
    except ProviderDown:
        return "general_civic", "fallback"
