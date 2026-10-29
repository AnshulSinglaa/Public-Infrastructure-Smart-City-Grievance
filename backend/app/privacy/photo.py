"""Photo privacy: re-encode (drops EXIF/GPS), shrink, and make a blurred copy to share."""

import io
import logging
from functools import cache

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from app.config import settings

log = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse "decompression bombs" (tiny file, huge image)

# Faces, plus number plates. Honest note: the plate cascade was trained on Russian plates
# and misses many Indian ones (other fonts, two-line plates, angles). It is a best effort,
# not a guarantee; a proper plate detector is a later upgrade.
CASCADES = ["haarcascade_frontalface_default.xml", "haarcascade_russian_plate_number.xml"]


def clean(data: bytes) -> Image.Image:
    """Decode, apply the EXIF rotation, keep only RGB pixels, shrink to <= PHOTO_MAX_PX.
    Raises OSError/ValueError if the bytes are not a readable image."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGB")
    img.thumbnail((settings.photo_max_px, settings.photo_max_px))
    return img


def to_jpeg(img: Image.Image) -> bytes:
    """A fresh JPEG from the pixels only: no exif= argument, so no EXIF/GPS is written."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


@cache
def cascades() -> list:
    found = []
    for name in CASCADES:
        c = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if c.empty():
            raise RuntimeError(f"cascade file missing: {name}")
        found.append(c)
    return found


def find_boxes(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) of every face / plate the cascades find."""
    gray = np.asarray(img.convert("L"))
    return [tuple(int(v) for v in box) for c in cascades()
            for box in c.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)]


def blur(img: Image.Image) -> Image.Image:
    out = img.copy()
    for x, y, w, h in find_boxes(img):
        box = (x, y, x + w, y + h)
        out.paste(out.crop(box).filter(ImageFilter.GaussianBlur(max(w, h) // 4 + 2)), box)
    return out


def blurred_jpeg(img: Image.Image) -> bytes | None:
    """The shareable copy, or None if blurring failed (then the photo is withheld)."""
    try:
        return to_jpeg(blur(img))
    except Exception:  # fail closed: any error means "don't share it"
        log.exception("blur failed; photo will be withheld")
        return None
