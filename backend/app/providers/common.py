"""Shared by both model providers: prompts, JSON cache, retries/backoff, validation, tracing.

Time budget per model call (B-031), so a report answers well inside the UI's 90 s:
- each request times out after LLM_TIMEOUT_S (20 s); at most TRIES (2) requests;
- a retry only starts if it can still finish inside BUDGET_S (50 s) from the first try,
  honouring the provider's Retry-After on a 429 when that fits;
- the one "invalid JSON" retry is skipped once JSON_RETRY_BEFORE_S (25 s) have passed.
The image and text calls run in parallel, so the report's total is about one budget.
"""

import hashlib
import json
import logging
import time
from functools import cache
from pathlib import Path

from pydantic import ValidationError

from app import tracing
from app.config import settings
from app.intake.catalog import categories
from app.providers.schemas import unsure

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
TRIES = 2
BACKOFF_S = 1.0  # wait before the retry when the provider gives no Retry-After; tests set 0
BUDGET_S = 50.0
JSON_RETRY_BEFORE_S = 25.0


class Retryable(Exception):
    """429, 5xx or a timeout: worth trying again. retry_after = the provider's hint (s)."""

    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderDown(Exception):
    """Still failing after the retries (or a non-retryable error such as a bad API key)."""


@cache
def load_prompt(name: str) -> tuple[str, str]:
    """prompts/<name>.md = 'version: N', a '---' line, then the system prompt."""
    head, body = (PROMPTS / f"{name}.md").read_text(encoding="utf-8").split("---\n", 1)
    version = head.strip().removeprefix("version:").strip()
    listing = "\n".join(f"- {code}: {row['name_en']} ({row['photo_cues']})"
                        for code, row in categories().items())
    return version, body.replace("{categories}", listing)


def cache_path(parts: list[bytes]) -> Path:
    return settings.llm_cache_dir / (hashlib.sha256(b"\x00".join(parts)).hexdigest() + ".json")


def with_backoff(call, started: float | None = None):
    """Run call(); on Retryable wait (Retry-After, else BACKOFF_S) and try once more, but only
    if that retry can finish inside the budget. Raises ProviderDown otherwise."""
    started = time.monotonic() if started is None else started
    for attempt in range(TRIES):
        try:
            return call()
        except Retryable as e:
            wait = e.retry_after if e.retry_after is not None else BACKOFF_S
            elapsed = time.monotonic() - started
            fits = elapsed + wait + settings.llm_timeout_s <= BUDGET_S
            if attempt == TRIES - 1 or not fits:
                raise ProviderDown(f"{type(e.__cause__ or e).__name__} after "
                                   f"{attempt + 1} tries, {elapsed:.1f} s") from None
            time.sleep(wait)


def run(name: str, schema, call, *, model: str, prompt_version: str, system: str,
        input_bytes: bytes, report_id: str | None, trace_input) -> dict:
    """Cache -> model call (with backoff) -> validate -> one retry on invalid -> 'unsure'.
    call() returns (raw_json_text, {"input": tokens, "output": tokens}).
    Raises ProviderDown when the provider is unreachable."""
    # The full system prompt is in the key (TL#3): editing it without bumping the version
    # can never reuse an answer to the old prompt.
    path = cache_path([input_bytes, model.encode(), system.encode()])
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    started = time.monotonic()
    for attempt in range(2):  # the first try + one retry when the JSON is invalid
        if attempt and time.monotonic() - started > JSON_RETRY_BEFORE_S:
            break  # no time left for a second answer: go with "unsure"
        with tracing.generation(name, model, prompt_version, report_id, trace_input) as span:
            try:
                raw, usage = with_backoff(call, started)
                result = schema.model_validate_json(raw).model_dump()
            except (ValidationError, ValueError) as e:
                log_failure(name, model, f"invalid output ({type(e).__name__})", started, trace_input)
                if span:
                    span.update(level="WARNING", status_message=f"invalid output: {type(e).__name__}")
                continue
            except ProviderDown as e:
                log_failure(name, model, str(e), started, trace_input)
                if span:
                    span.update(level="ERROR", status_message="provider down")
                raise
            if span:
                span.update(output=result, usage_details=usage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")  # only valid answers are cached
        return result
    return unsure(schema)


def log_failure(name: str, model: str, error: str, started: float, trace_input) -> None:
    """One server-log line per provider failure, Langfuse or not (B-038). trace_input is
    already masked text (or a '<photo, N bytes>' note), and is cut to 80 characters."""
    log.warning("provider failure: step=%s model=%s error=%s latency=%.1fs input=%r", name,
                model, error, time.monotonic() - started, str(trace_input)[:80])
