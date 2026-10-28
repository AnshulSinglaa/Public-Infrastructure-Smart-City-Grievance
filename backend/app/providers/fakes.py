"""Fake AI providers for Sprint 2: fixed answers, no network."""


def fake_intake(photo: bytes | None, text: str | None) -> dict:
    """Stand-in for the vision/text model: always a high-severity pothole."""
    return {"category": "pothole", "severity": "high", "confidence": 0.9, "fake": True}
