"""Shared test helpers (not in conftest.py: importing from conftest breaks if ui/tests
ever gets its own conftest)."""

import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)
COVERED = {"lat": "28.544", "lng": "77.311"}  # inside covered ward 185


def jpeg(size=(2000, 1500), gps=True) -> bytes:
    """A noisy JPEG, by default with camera + GPS EXIF (what a phone gallery photo carries)."""
    img = Image.effect_noise(size, 60).convert("RGB")
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    if gps:
        exif.get_ifd(0x8825).update({1: "N", 2: (28.0, 32.0, 38.4), 3: "E", 4: (77.0, 18.0, 0.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()
