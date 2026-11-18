"""Intake text tasks: text -> TextAssessment, free-text answer -> option.
Both go through the LLM gateway (Groq TEXT_MODEL first, Gemini if Groq fails).
The caller passes MASKED text."""

from app.llm import gateway
from app.providers.common import load_prompt
from app.providers.schemas import AnswerChoice, TextAssessment


def user_block(text: str) -> str:
    """The citizen's words go inside a delimited block; a fake closing tag in the text is
    removed so it can't 'end' the block early (prompt injection)."""
    text = text.replace("</user_text>", "").replace("<user_text>", "")
    return f"<user_text>\n{text}\n</user_text>"


def assess_text(text: str, report_id: str | None = None) -> dict:
    """Raises ProviderDown when neither Groq nor Gemini answers."""
    version, system = load_prompt("assess_text")
    return gateway.complete("assess_text", TextAssessment, system, user_block(text),
                            prompt_version=version, report_id=report_id, trace_input=text)[0]


def map_answer(text: str, options: dict[str, str], report_id: str | None = None) -> str:
    """Free-text answer ('bses wala, laal logo') -> one of options {value: label}, or
    'unclear'. The caller passes MASKED text; the model sees only the option labels,
    never routing data. Raises ProviderDown when no provider answers."""
    version, system = load_prompt("map_answer")
    listing = "".join(f"- {value}: {label}\n" for value, label in options.items())
    user = f"Options:\n{listing}\n" + user_block(text)
    out = gateway.complete("map_answer", AnswerChoice, system, user, prompt_version=version,
                           report_id=report_id, trace_input=user)[0]
    return out["value"] if out["value"] in options else "unclear"
