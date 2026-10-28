"""Fill the bilingual complaint templates (string.Template: $name placeholders)."""

from pathlib import Path
from string import Template

TEMPLATES = Path(__file__).parent / "templates"
CATEGORY_HI = {"pothole": "सड़क में गड्ढे"}
SEVERITY_HI = {"low": "कम", "medium": "मध्यम", "high": "अधिक"}


def render(lang: str, values: dict) -> str:
    text = (TEMPLATES / f"complaint_{lang}.txt").read_text(encoding="utf-8")
    # safe_substitute: a missing value leaves "$name" in the text instead of crashing.
    return Template(text).safe_substitute(values)


def build_complaint(category: str, severity: str, route: dict, user_text: str | None) -> dict:
    values = {
        "ward_no": route["ward_no"],
        "ward_name": route["ward_name"],
        "sla_days": route["sla_days"],
        "category_en": category.replace("_", " "),
        "category_hi": CATEGORY_HI.get(category, category),
        "severity_en": severity,
        "severity_hi": SEVERITY_HI.get(severity, severity),
        "user_text_en": f'Reporter\'s words: "{user_text}"' if user_text else "",
        "user_text_hi": f'शिकायतकर्ता के शब्द: "{user_text}"' if user_text else "",
    }
    return {"hi": render("hi", values), "en": render("en", values)}
