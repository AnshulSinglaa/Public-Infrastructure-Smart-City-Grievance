"""Scripted stand-ins for Gemini and Groq (PROVIDER_MODE=fake, used by every test).

A test calls script(image=[...], text=[...]); each call to the model takes the next item:
  a dict      -> returned as the model's JSON
  "invalid"   -> returned as broken JSON
  "down"      -> a retryable error (like a 503)
  "slow"      -> waits the whole request timeout, then a retryable error (like a timeout)
  "429:N"     -> a 429 rate limit asking us to wait N seconds (Retry-After)
When the list is empty, the default answer (a confident pothole) is used.
They go through the same cache / validation / retry code as the real providers.
"""

import json
import time

from app.config import settings
from app.providers.common import Retryable

IMAGE = {"category": "POTHOLE", "confidence": 0.9, "junk": False, "junk_reason": None,
         "emergency": False, "emergency_kind": None, "drain_type": "none",
         "road_judgement": "lane", "face_boxes": [], "plate_boxes": [], "is_dark": False}
TEXT = {"category": "POTHOLE", "confidence": 0.9, "severity": "high", "emergency": False,
        "emergency_kind": None, "landmark": None}

queue: dict[str, list] = {"image": [], "text": []}
calls = {"image": 0, "text": 0}


def script(image: list | None = None, text: list | None = None) -> None:
    queue["image"], queue["text"] = list(image or []), list(text or [])
    calls["image"] = calls["text"] = 0


def answer(kind: str, default: dict) -> tuple[str, dict]:
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
        return "{not json", {}
    return json.dumps(default | item), {"input": 0, "output": 0}


def image_call() -> tuple[str, dict]:
    return answer("image", IMAGE)


def text_call() -> tuple[str, dict]:
    return answer("text", TEXT)
