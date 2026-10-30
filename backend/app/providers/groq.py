"""Text -> TextAssessment with Groq (TEXT_MODEL). The caller passes MASKED text."""

from functools import cache

from app.config import settings
from app.providers import fakes
from app.providers.common import ProviderDown, Retryable, load_prompt, run
from app.providers.schemas import TextAssessment


@cache
def client():
    from groq import Groq  # absolute import: the groq package, not this module

    # max_retries=0: our own backoff in common.py does the retrying.
    return Groq(api_key=settings.groq_api_key.get_secret_value(), max_retries=0,
                timeout=settings.llm_timeout_s)


def user_block(text: str) -> str:
    """The citizen's words go inside a delimited block; a fake closing tag in the text is
    removed so it can't 'end' the block early (prompt injection)."""
    text = text.replace("</user_text>", "").replace("<user_text>", "")
    return f"<user_text>\n{text}\n</user_text>"


def call_groq(system: str, text: str) -> tuple[str, dict]:
    import groq

    try:
        resp = client().chat.completions.create(
            model=settings.text_model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user_block(text)}])
    except groq.RateLimitError as e:  # honour the wait Groq asks for (B-031)
        raise Retryable("429", retry_after=retry_after(e.response.headers)) from e
    except (groq.InternalServerError, groq.APITimeoutError, groq.APIConnectionError) as e:
        raise Retryable from e
    except groq.APIError as e:  # bad key, bad request: retrying won't help
        raise ProviderDown(f"groq {type(e).__name__}") from e
    usage = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens}
    return resp.choices[0].message.content or "", usage


def retry_after(headers) -> float | None:
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):  # missing, or an HTTP date we don't parse
        return None


def assess_text(text: str, report_id: str | None = None) -> dict:
    """Raises ProviderDown when Groq can't be reached after the retries."""
    version, system = load_prompt("assess_text")
    fake = settings.provider_mode == "fake"
    call = fakes.text_call if fake else (lambda: call_groq(system, text))
    return run("assess_text", TextAssessment, call,
               model="fake" if fake else settings.text_model, prompt_version=version,
               system=system, input_bytes=text.encode(), report_id=report_id, trace_input=text)
