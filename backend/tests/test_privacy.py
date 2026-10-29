import io

import cv2
import pytest
from api_helpers import jpeg
from PIL import Image

from app.privacy import photo
from app.privacy.text import decrypt, encrypt, mask_pii


@pytest.mark.parametrize("text, expected", [
    ("call 98765 43210", "call 98XXXXXX10"),
    ("फोन ९८७६५४३२१० पर", "फोन 98XXXXXX10 पर"),
    ("nau aath saat chhe paanch chaar teen do ek shunya", "98XXXXXX10"),
    ("नौ आठ सात छह पांच चार तीन दो एक शून्य", "98XXXXXX10"),
    ("nine eight seven six five, four three two one zero", "98XXXXXX10"),
    ("+91-98765-43210", "98XXXXXX10"),
    ("98765  43210", "98XXXXXX10"),
    ("98765.43210", "98XXXXXX10"),
    ("+91.98765.43210", "98XXXXXX10"),
    ("mail priya.k@gmail.com please", "mail p***@g***.com please"),
    ("do gaddhe, ek bada", "do gaddhe, ek bada"),  # short number words stay words
    # "दोनों" (both) starts with "दो" + a vowel sign: must not count as a number word
    ("दोनों एक दो तीन चार पांच छह सात आठ", "दोनों एक दो तीन चार पांच छह सात आठ"),
    ("ward 185, 2 potholes", "ward 185, 2 potholes"),  # short numbers stay
    # B-017: the user's own Devanagari digits stay as written; only the phone is masked
    ("वार्ड १८५ में do gaddhe hain", "वार्ड १८५ में do gaddhe hain"),
    ("वार्ड १८५, फोन ९८७६५४३२१०", "वार्ड १८५, फोन 98XXXXXX10"),
    ("ek do teen char paanch chhe saat aath nau", "ek do teen char paanch chhe saat aath nau"),
    # B-022: house / flat numbers
    ("mera naam Ramesh Kumar, H.No. 45B Gali 3", "mera naam Ramesh Kumar, H.No. XX Gali 3"),
    ("house no 12/3 ke saamne", "house no XX ke saamne"),
    ("flat 402, Sector 5", "flat XX, Sector 5"),
    ("near #12-B", "near #XX"),
    # B-027: Devanagari and Hinglish house numbers
    ("मकान नं. 45 के सामने", "मकान नं. XX के सामने"),
    ("मकान नंबर ४५", "मकान नंबर XX"),
    ("म.नं. 12/3", "म.नं. XX"),
    ("फ्लैट 402 के नीचे", "फ्लैट XX के नीचे"),
    ("मकान 45 के पास", "मकान XX के पास"),
    ("makaan no. 5 ke saamne", "makaan no. XX ke saamne"),
    ("makan number 12B", "makan number XX"),
    ("ghar no 7", "ghar no XX"),
    ("मकान के सामने गड्ढा", "मकान के सामने गड्ढा"),  # no number: nothing to mask
])
def test_mask_pii(text, expected):
    assert mask_pii(text) == expected


def test_encryption_round_trip():
    token = encrypt("मेरा नंबर 9876543210")
    assert b"9876543210" not in token
    assert decrypt(token) == "मेरा नंबर 9876543210"
    assert decrypt(memoryview(token)) == "मेरा नंबर 9876543210"  # what psycopg hands back


def test_clean_strips_exif_and_shrinks():
    data = jpeg()
    assert Image.open(io.BytesIO(data)).getexif().get_ifd(0x8825)  # the input has GPS
    out = photo.to_jpeg(photo.clean(data))
    img = Image.open(io.BytesIO(out))
    assert max(img.size) == 1280
    assert not img.getexif() and b"Exif" not in out


def test_blur_changes_only_the_boxes(monkeypatch):
    img = photo.clean(jpeg(size=(400, 300), gps=False))
    monkeypatch.setattr(photo, "find_boxes", lambda im: [(10, 10, 100, 100)])
    out = photo.blur(img)
    assert out.crop((10, 10, 110, 110)).tobytes() != img.crop((10, 10, 110, 110)).tobytes()
    assert out.crop((200, 150, 400, 300)).tobytes() == img.crop((200, 150, 400, 300)).tobytes()


def test_blur_failure_returns_none(monkeypatch):
    def broken(im):
        raise RuntimeError("boom")

    monkeypatch.setattr(photo, "find_boxes", broken)
    assert photo.blurred_jpeg(photo.clean(jpeg(gps=False))) is None


@pytest.mark.skipif(not hasattr(cv2, "CascadeClassifier"),
                    reason="this OpenCV build has no Haar cascades (OpenCV 5 dropped them)")
def test_real_cascades_load_and_run():
    assert photo.find_boxes(photo.clean(jpeg(gps=False))) == []  # noise: no faces/plates
