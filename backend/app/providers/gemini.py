"""Photo -> ImageAssessment with Gemini (VISION_MODEL). Only the cleaned, EXIF-free JPEG
is sent, never the original upload."""

from functools import cache

from app.config import settings
from app.providers import fakes
from app.providers.common import ProviderDown, Retryable, load_prompt, run
from app.providers.schemas import ImageAssessment


@cache
def client():
    from google import genai  # slow import; only needed in live mode

    return genai.Client(api_key=settings.google_api_key.get_secret_value())


def call_gemini(system: str, img_bytes: bytes) -> tuple[str, dict]:
    from google.genai import errors, types

    config = types.GenerateContentConfig(
        system_instruction=system, response_mime_type="application/json", temperature=0,
        http_options=types.HttpOptions(timeout=settings.llm_timeout_s * 1000))
    try:
        resp = client().models.generate_content(
            model=settings.vision_model, config=config,
            contents=[types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                      "Assess this photo."])
    except errors.APIError as e:
        if e.code == 429 or e.code >= 500:
            raise Retryable from e
        raise ProviderDown(f"gemini {e.code}") from e
    except Exception as e:
        raise Retryable from e
    meta = resp.usage_metadata
    usage = {"input": meta.prompt_token_count or 0, "output": meta.candidates_token_count or 0}
    return resp.text or "", usage


def assess_image(img_bytes: bytes, report_id: str | None = None) -> dict:
    """Raises ProviderDown when Gemini can't be reached after the retries.
    (The category list comes from prompts/assess_image.md + data/categories.csv.)"""
    version, system = load_prompt("assess_image")
    fake = settings.provider_mode == "fake"
    call = fakes.image_call if fake else (lambda: call_gemini(system, img_bytes))
    return run("assess_image", ImageAssessment, call,
               model="fake" if fake else settings.vision_model, prompt_version=version,
               system=system, input_bytes=img_bytes, report_id=report_id,
               trace_input=f"<photo, {len(img_bytes)} bytes, not sent to tracing>")
