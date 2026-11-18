"""LLM gateway (HLD-S6 §2): every text-model call goes through `complete()`.

    cache hit? -> answer
    Groq (TEXT_MODEL, or the small INTENT_MODEL)  --timeout / 429 / 5xx / connection / auth-->
    Gemini (FALLBACK_MODEL, >= 20 s timeout)      --fails-->  ProviderDown ("try later")

- LiteLLM is used as a plain library (`litellm.completion`), one provider at a time: no proxy,
  no Router config. Our own loop below is the fallback, so it is easy to read and to test.
- Circuit breaker per MODEL (B-057): 3 failures in 60 s -> that model is skipped for 60 s, so
  a failing intent model never sends answers to Gemini. A model the provider doesn't serve
  (404) is a config error: logged as an error and skipped at once.
- Free-tier throttle (B-056): we count each Groq model's tokens (8k/min) and Gemini's requests
  over the last 60 s, and skip a model that would go over. If every model is full, Busy(N)
  tells the user "busy, try again in N s". A 429 with Retry-After <= 3 s is retried once.
- Deadline per call = Groq's timeout + Gemini's timeout + 5 s, so the fallback always gets
  its turn (intake: 20 + 20 + 5; Q&A: 8 + 20 + 5) (REVIEW R-04).
- A 400 caused by our own request is a bug: ProviderDown at once, no fallback.
- The JSON cache, schema check, "invalid JSON -> one retry" and the Langfuse generation span
  are the shared ones in providers/common.run (tracing.py; LiteLLM gives us the cost).
- PROVIDER_MODE=fake: providers/fakes.py answers instead of LiteLLM (same loop, breaker and
  throttle), so tests can script "Groq down, Gemini answers" without any network.
- Vision stays in providers/gemini.py (one provider, no fallback, as before).

ponytail: the breaker and the throttle live in this process's memory; with many API
instances they would need a shared table in Postgres.
"""

import logging
import math
import os
import time
from functools import lru_cache

from app.config import settings
from app.providers import fakes
from app.providers.common import ProviderDown, Retryable, run

log = logging.getLogger(__name__)

MAX_RETRY_AFTER_S = 3.0  # a 429 asking us to wait longer -> go to the next provider
BREAKER_FAILS, WINDOW_S = 3, 60.0
failures: dict[str, list[float]] = {}  # model -> failure times (the breaker)
spent: dict[str, list[tuple[float, int]]] = {}  # model -> (time, tokens) of recent calls
# Before LiteLLM is ever imported: in its default "DEV" mode it loads .env into os.environ
# (our secrets, and it leaks into every later Settings()); and no price-list download.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


class Busy(ProviderDown):
    """Every model is at its per-minute free-tier limit: try again in `retry_in` seconds."""

    def __init__(self, retry_in: int):
        super().__init__(f"busy, try again in {retry_in} s")
        self.retry_in = retry_in


class ModelMissing(Retryable):
    """The provider doesn't serve this model (404): a config error, not an outage."""


def chain(task: str, timeout: float) -> list[tuple[str, str, str, float]]:
    """(provider, LiteLLM model name, API key, timeout) in the order we try them."""
    groq_model = settings.intent_model if task == "intent" else settings.text_model
    return [("groq", f"groq/{groq_model}", settings.groq_api_key.get_secret_value(), timeout),
            ("gemini", f"gemini/{settings.fallback_model}",
             settings.google_api_key.get_secret_value(),
             max(timeout, settings.fallback_timeout_s))]


def cooling(model: str) -> bool:
    now = time.monotonic()
    failures[model] = [t for t in failures.get(model, []) if now - t < WINDOW_S]
    return len(failures[model]) >= BREAKER_FAILS


def wait_for_room(provider: str, model: str, tokens: int) -> int:
    """Seconds until `tokens` more fit in this model's per-minute limit (0 = now)."""
    now = time.monotonic()
    calls = spent[model] = [c for c in spent.get(model, []) if now - c[0] < WINDOW_S]
    full = (sum(t for _, t in calls) + tokens > settings.groq_tpm if provider == "groq"
            else len(calls) >= settings.gemini_rpm)
    return max(1, math.ceil(WINDOW_S - (now - calls[0][0]))) if full and calls else 0


def litellm_call(model: str, key: str, system: str, user: str, timeout: float):
    import litellm  # slow import (~2-8 s): only in live mode

    litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banners (B-069)
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    extra = {"temperature": 0} if model.startswith("groq/") else {}  # Gemini 3: deprecated
    if "gpt-oss" in model:
        extra["reasoning_effort"] = "low"  # reasoning tokens count against the 8k/min
    try:
        resp = litellm.completion(
            model=model, api_key=key, timeout=timeout, num_retries=0,
            response_format={"type": "json_object"}, **extra,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    except litellm.NotFoundError as e:
        raise ModelMissing(f"{model} not found") from e
    except litellm.BadRequestError as e:  # our own request is wrong: a bug, not an outage
        raise ProviderDown(f"{model} {type(e).__name__}") from e
    except litellm.RateLimitError as e:
        raise Retryable("429", retry_after=retry_after(getattr(e, "response", None))) from e
    except Exception as e:  # timeout, 5xx, connection, auth: try the next one
        raise Retryable(f"{type(e).__name__}: {str(e)[:200]}") from e
    try:
        cost = litellm.completion_cost(resp)
    except Exception:  # noqa: BLE001 - a model missing from LiteLLM's price list
        cost = 0.0
    usage = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens,
             "cost": cost}
    return resp.choices[0].message.content or "", usage


def retry_after(response) -> float | None:
    try:
        return float(response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None


def chat(task: str, system: str, user: str, timeout: float) -> tuple[str, dict]:
    """One answer from the first model that works and has room. Raises Busy when every
    model is at its per-minute limit, ProviderDown when none answers."""
    models = chain(task, timeout)
    started, deadline = time.monotonic(), sum(m[3] for m in models) + 5
    guess = (len(system) + len(user)) // 3 + 500  # rough tokens in + out, for the throttle
    waits = []
    for provider, model, key, limit in models:
        if cooling(model):
            continue
        if wait := wait_for_room(provider, model, guess):
            waits.append(wait)
            continue
        for attempt in range(2):  # the second try only after a short Retry-After
            left = deadline - (time.monotonic() - started)
            if left <= 0:
                raise ProviderDown("gateway deadline")
            try:
                if settings.provider_mode == "fake":
                    raw, usage = fakes.chat(task, provider, user)
                else:
                    raw, usage = litellm_call(model, key, system, user, min(limit, left))
                spent[model].append((time.monotonic(), usage["input"] + usage["output"]))
                return raw, usage | {"provider": provider}
            except ModelMissing:
                log.error("gateway: %s is not served: fix the model setting", model)
                failures[model] = failures[model] + [time.monotonic()] * BREAKER_FAILS
                break
            except Retryable as e:
                wait = e.retry_after
                if attempt == 0 and wait is not None and wait <= MAX_RETRY_AFTER_S \
                        and wait + limit < left:
                    time.sleep(wait)
                    continue
                failures[model].append(time.monotonic())
                log.warning("gateway: %s failed for %s (%s), trying the next one", model,
                            task, e)
                break
    if waits:
        raise Busy(min(waits))
    raise ProviderDown("every provider failed or is cooling down")


def complete(task: str, schema, system: str, user: str, *, prompt_version: str,
             report_id: str | None, trace_input, timeout: float | None = None,
             default: dict | None = None) -> tuple[dict, dict]:
    """Cache -> chat() -> schema check (one retry on invalid JSON) -> default.
    Returns (result, info): info = {"provider": "groq" / "gemini" / None (a cache hit or the
    default), "tokens": input + output tokens used}. The caller passes MASKED text only."""
    info = {"provider": None, "tokens": 0}

    def call():
        raw, usage = chat(task, system, user, timeout or settings.llm_timeout_s)
        info["provider"] = usage["provider"]
        info["tokens"] += usage["input"] + usage["output"]
        return raw, usage

    model = "fake" if settings.provider_mode == "fake" else chain(task, 0)[0][1]
    result = run(task, schema, call, model=model, prompt_version=prompt_version, system=system,
                 input_bytes=user.encode(), report_id=report_id, trace_input=trace_input,
                 default=default)
    return result, info


# ---------- embeddings (Gemini, dense) ----------

def normalised(vector) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def embed(texts: list[str], task_type: str = "RETRIEVAL_QUERY") -> list[list[float]]:
    """768-d unit vectors (gemini-embedding-001, truncated + re-normalised, HLD §3.5).
    MASKED text only. Raises ProviderDown when Gemini can't embed (callers degrade)."""
    if settings.provider_mode == "fake":
        return [fakes.embed(t) for t in texts]
    from google.genai import types

    from app.providers.gemini import client

    out = []
    try:
        for i in range(0, len(texts), 100):  # the API takes up to 100 texts per call
            resp = client().models.embed_content(
                model=settings.embed_model, contents=texts[i:i + 100],
                config=types.EmbedContentConfig(task_type=task_type,
                                                output_dimensionality=settings.embed_dims))
            out += [normalised(e.values) for e in resp.embeddings]
    except Exception as e:
        raise ProviderDown(f"embedding {type(e).__name__}") from e
    return out


@lru_cache(maxsize=512)
def embed_query(text: str) -> tuple[float, ...]:
    """One question's vector, cached in this process (HLD §6 caching)."""
    return tuple(embed([text])[0])
