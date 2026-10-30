"""Fill the English complaint template (string.Template: $name placeholders).
English only (UX §14); a Hindi "Translate" action may come back later."""

from pathlib import Path
from string import Template

from app.intake.catalog import categories

TEMPLATE = Path(__file__).parent / "templates" / "complaint_en.txt"


def build_complaint(category: str, severity: str, route: dict, citizen_text: str | None,
                    emergency: bool = False) -> str:
    """citizen_text must already be masked; it is quoted as written (Hindi/Hinglish too)."""
    values = {
        "ward_no": route["ward_no"],
        "ward_name": route["ward_name"],
        "sla_days": route["sla_days"],
        "category_en": categories()[category]["name_en"].lower(),
        "severity_en": severity,
        "user_text_en": f'Reporter\'s words: "{citizen_text}"' if citizen_text else "",
        "action_en": ("Immediate action requested (safety risk)." if emergency
                      else f"Please get it fixed within {route['sla_days']} days."),
    }
    # safe_substitute: a missing value leaves "$name" in the text instead of crashing.
    return Template(TEMPLATE.read_text(encoding="utf-8")).safe_substitute(values)
