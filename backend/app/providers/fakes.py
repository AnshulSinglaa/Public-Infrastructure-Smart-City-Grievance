"""Scripted stand-ins for Gemini and Groq (PROVIDER_MODE=fake, used by every test).

A test calls script(image=[...], text=[...]); each call to the model takes the next item:
  a dict      -> returned as the model's JSON
  "invalid"   -> returned as broken JSON
  "down"      -> a retryable error (like a 503)
  "slow"      -> waits the whole request timeout, then a retryable error (like a timeout)
  "429:N"     -> a 429 rate limit asking us to wait N seconds (Retry-After)
When the list is empty, the default answer (a confident pothole) is used.
They go through the same cache / validation / retry code as the real providers, and text
calls go through the LLM gateway's Groq -> Gemini loop: each provider try takes the next item,
so ["down"] = "Groq down, Gemini answers" and ["down", "down"] = both down.
script(qa=[...], intent=[...]) does the same for Q&A answers and the small intent model.
`log` lists every text call as (task, provider).

Embeddings, BM25 and the reranker are faked too: hashed bags of words, so texts sharing
words get similar vectors / scores, with no network and no model download.
"""

import hashlib
import json
import math
import re
import time

from app.config import settings
from app.providers.common import Retryable

IMAGE = {"category": "POTHOLE", "confidence": 0.9, "junk": False, "junk_reason": None,
         "emergency": False, "emergency_kind": None, "drain_type": "none",
         "road_judgement": "lane", "face_boxes": [], "plate_boxes": [], "is_dark": False}
TEXT = {"category": "POTHOLE", "confidence": 0.9, "severity": "high", "emergency": False,
        "emergency_kind": None, "landmark": None}

QA = {"status": "not_found", "sentences": []}
INTENT = {"label": "general_civic"}

queue: dict[str, list] = {"image": [], "text": [], "qa": [], "intent": []}
calls = {"image": 0, "text": 0}
log: list[tuple[str, str]] = []
qa_inputs: list[str] = []  # what the Q&A model was sent (tests check it)


# The free-text answer mapper (/answer with "text"): default answer is "unclear".
answers: list = []
answer_inputs: list[str] = []  # what the mapper was sent (tests check it is masked)


def script(image: list | None = None, text: list | None = None,
           answer: list | None = None, qa: list | None = None,
           intent: list | None = None) -> None:
    queue["image"], queue["text"] = list(image or []), list(text or [])
    queue["qa"], queue["intent"] = list(qa or []), list(intent or [])
    calls["image"] = calls["text"] = 0
    log.clear()
    qa_inputs.clear()
    answers[:] = list(answer or [])
    answer_inputs.clear()


def answer(kind: str, default: dict) -> tuple[str, dict]:
    if kind in calls:
        calls[kind] += 1
    item = queue[kind].pop(0) if queue[kind] else {}
    if item == "down":
        raise Retryable("scripted outage")
    if item == "slow":
        time.sleep(settings.llm_timeout_s)
        raise Retryable("scripted timeout")
    if isinstance(item, str) and item.startswith("429:"):
        raise Retryable("scripted 429", retry_after=float(item[4:]))
    if item == "invalid":
        return "{not json", {"input": 0, "output": 0}
    return json.dumps(default | item), {"input": 0, "output": 0}


def image_call() -> tuple[str, dict]:
    return answer("image", IMAGE)


def text_call() -> tuple[str, dict]:
    return answer("text", TEXT)


def answer_call(user: str) -> tuple[str, dict]:
    answer_inputs.append(user)
    value = answers.pop(0) if answers else "unclear"
    return json.dumps({"value": value}), {"input": 0, "output": 0}


def chat(task: str, provider: str, user: str) -> tuple[str, dict]:
    """The gateway's stand-in for one LiteLLM call to `provider`."""
    log.append((task, provider))
    if task == "map_answer":
        return answer_call(user)
    if task == "qa_answer":
        qa_inputs.append(user)
        return answer("qa", QA)
    if task == "intent":
        return answer("intent", INTENT)
    return answer("text", TEXT)


WORD = re.compile(r"\w+")


def words(text: str) -> list[str]:
    return [w.casefold() for w in WORD.findall(text)]


def bucket(word: str, size: int) -> int:
    return int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % size


def embed(text: str, dims: int = 768) -> list[float]:
    vec = [0.0] * dims
    for w in words(text):
        vec[bucket(w, dims)] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def sparse(text: str) -> tuple[list[int], list[float]]:
    """(indices, values) like fastembed BM25: one entry per distinct word."""
    counts: dict[int, float] = {}
    for w in words(text):
        counts[bucket(w, 2**31)] = counts.get(bucket(w, 2**31), 0.0) + 1.0
    return list(counts), list(counts.values())


class Reranker:
    """Score = how many of the question's words the chunk has."""

    def rerank(self, query: str, documents: list[str]):
        q = set(words(query))
        return [float(len(q & set(words(d)))) for d in documents]
