"""What the models must return. Anything that doesn't validate counts as invalid output.
Category codes are data/categories.csv `code`, exactly as written ('POTHOLE').
Extra keys (say, a "department") are ignored: they are never read."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.intake.catalog import categories

Confidence = Annotated[float, Field(ge=0, le=1)]
# [ymin, xmin, ymax, xmax], each 0-1000 (Gemini's own box format)
Box = Annotated[list[Annotated[int, Field(ge=0, le=1000)]], Field(min_length=4, max_length=4)]
EmergencyKind = Literal["live_wire", "gas_leak", "open_manhole", "fallen_tree",
                        "building_collapse", "fire", "road_cave_in", "other"]


class Assessment(BaseModel):
    category: str | None
    confidence: Confidence
    emergency: bool = False
    emergency_kind: EmergencyKind | None = None

    @model_validator(mode="before")
    @classmethod
    def known_category(cls, data):
        """data/categories.csv is the only list of codes. A code that isn't in it counts as
        'unsure' (no category, confidence 0), which makes the graph ask the user."""
        if isinstance(data, dict) and data.get("category") is not None:
            code = str(data["category"]).strip()
            data = data | ({"category": code} if code in categories()
                           else {"category": None, "confidence": 0})
        return data


class ImageAssessment(Assessment):
    junk: bool = False
    junk_reason: Literal["selfie", "meme", "screenshot", "blank", "unrelated"] | None = None
    drain_type: Literal["open_nala", "sewer_manhole", "none", "unknown"] | None = None
    road_judgement: Literal["main_road", "lane", "unknown"] | None = None
    face_boxes: list[Box] = []
    plate_boxes: list[Box] = []
    is_dark: bool = False


class TextAssessment(Assessment):
    severity: Literal["low", "medium", "high", "critical"] | None = None
    landmark: str | None = Field(None, max_length=200)  # TL#5: model output is capped too


def unsure(schema: type[Assessment]) -> dict:
    """What we use when the model's output stays invalid: no category, confidence 0."""
    return schema(category=None, confidence=0).model_dump()
