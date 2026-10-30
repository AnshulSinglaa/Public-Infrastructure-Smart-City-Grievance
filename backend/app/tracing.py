"""Langfuse tracing: one trace per report, one 'generation' span per model call.
No LANGFUSE_PUBLIC_KEY -> tracing is off and every helper here is a no-op.
Callers pass only masked text and never photo bytes."""

import logging
from contextlib import contextmanager
from functools import cache

from app.config import settings

log = logging.getLogger(__name__)


@cache
def client():
    if not settings.langfuse_public_key:
        return None
    from langfuse import Langfuse  # imported only when tracing is on

    return Langfuse(public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key.get_secret_value(),
                    host=settings.langfuse_host)


@contextmanager
def generation(name: str, model: str, prompt_version: str, report_id: str | None, input):
    """Yields the span (call .update(...) on it) or None when tracing is off.
    The trace id is derived from the report id, so a report's first call and its
    clarify-resume land in the same trace."""
    lf = client()
    if lf is None:
        yield None
        return
    context = {"trace_id": lf.create_trace_id(seed=report_id)} if report_id else None
    with lf.start_as_current_observation(as_type="generation", name=name, model=model,
                                         version=prompt_version, input=input,
                                         trace_context=context) as span:
        yield span


def flush() -> None:
    if client():
        client().flush()
