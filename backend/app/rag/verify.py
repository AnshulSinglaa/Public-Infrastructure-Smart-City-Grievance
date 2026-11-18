"""Quote and number checks (HLD §4 "citation exactness", contract §6). Plain code, no model.

A quote counts only if its exact words are in the source after normalise() (the same
function ingest uses), compared without case. 4-40 words, or 1-3 words when it is a name or
a number (a road name on a list page, "30 days" is 2 words with a number).
"""

import re

from app.rag.ingest import normalise

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def plain(text: str) -> str:
    """normalise() + no case + straight quotes, for comparing."""
    text = normalise(text).casefold()
    return text.replace("’", "'").replace("‘", "'").replace("“", '"') \
        .replace("”", '"')


def quote_ok(quote: str, source_text: str) -> bool:
    q = plain(quote).strip(" .,;:\"'")
    n = len(q.split())
    if n == 0 or n > 40:
        return False
    if n < 4 and not (any(c.isdigit() for c in q) or any(c.isupper() for c in quote)):
        return False
    return q in plain(source_text)


def numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in NUMBER.findall(normalise(text))}


def numbers_ok(sentence: str, quotes: list[str]) -> bool:
    """Every number in the sentence appears in one of its verified quotes."""
    return numbers(sentence) <= set().union(*(numbers(q) for q in quotes)) if quotes \
        else not numbers(sentence)
