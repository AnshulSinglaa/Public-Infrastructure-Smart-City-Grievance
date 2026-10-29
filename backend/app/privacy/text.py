"""Text privacy: mask phone numbers / emails, and encrypt text at rest (Fernet)."""

import re
from functools import cache

from cryptography.fernet import Fernet

from app.config import local_secret, settings

# ---------- masking ----------

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUMBER_WORDS = {
    "0": ["zero", "shunya", "shoonya", "sunya", "शून्य"],
    "1": ["one", "ek", "एक"],
    "2": ["two", "do", "दो"],
    "3": ["three", "teen", "tin", "तीन"],
    "4": ["four", "char", "chaar", "चार"],
    "5": ["five", "panch", "paanch", "पांच", "पाँच"],
    "6": ["six", "chhe", "chhah", "chah", "che", "छह", "छः", "छे"],
    "7": ["seven", "saat", "sat", "सात"],
    "8": ["eight", "aath", "ath", "आठ"],
    "9": ["nine", "nau", "नौ"],
}
WORD_DIGIT = {w: d for d, words in NUMBER_WORDS.items() for w in words}
# Longest first, so "chhah" wins over "chah". The lookarounds stand in for \b, which
# breaks on Devanagari vowel signs (Python doesn't count them as word characters).
_ALT = "|".join(sorted(map(re.escape, WORD_DIGIT), key=len, reverse=True))
WORDCHAR = rf"[\w{chr(0x0900)}-{chr(0x097F)}]"  # letters + the whole Devanagari block
TOKEN = re.compile(rf"(?<!{WORDCHAR})(?:{_ALT}|\d)(?!{WORDCHAR})", re.IGNORECASE)
# Only runs of 9+ number words become digits: "do gaddhe" (two potholes) must stay text.
RUN = re.compile(rf"{TOKEN.pattern}(?:[\s,.-]+{TOKEN.pattern}){{8,}}", re.IGNORECASE)

# Indian mobile: optional +91 / 0091 / 91 / 0, then 6-9 and nine more digits, with up to
# two spaces, dots or dashes between digits ("98765  43210", "+91.98765.43210").
# Known gap: a number mixing digits and words ("98765 chaar teen...") with fewer than
# 9 words is not joined, so it stays unmasked.
PHONE = re.compile(r"(?<!\d)(?:(?:\+|00)?91[\s.-]{0,2}|0)?[6-9](?:[\s.-]{0,2}\d){9}(?!\d)")
EMAIL = re.compile(r"([\w.%+-])[\w.%+-]*@(\w)[\w.-]*\.([A-Za-z]{2,})")
# House / flat numbers: "H.No. 45B", "house no 12/3", "flat 402", "#12-B", "makaan no. 5",
# "ghar no 7", "मकान नं. 45", "मकान नंबर ४५", "म.नं. 12", "फ्लैट 402", "मकान 45". The keyword
# stays, the number becomes XX (\d also matches Devanagari digits). Names can't be found
# reliably by a regex (known gap, see contract).
HOUSE_WORDS = [
    r"h\.?\s?no\.?", r"house\s+(?:no\.?|number)", r"flat(?:\s+no\.?)?", r"#",
    r"(?:makaan|makan|ghar)\s+(?:no\.?|number)",
    r"मकान(?:\s*(?:नं\.?|नंबर))?", r"म\.\s?नं\.?", r"फ्लैट(?:\s*(?:नं\.?|नंबर))?",
]
HOUSE = re.compile(rf"(?<!{WORDCHAR})((?:{'|'.join(HOUSE_WORDS)})\s*)[\w/-]*\d[\w/-]*",
                   re.IGNORECASE)


def words_to_digits(m: re.Match) -> str:
    return "".join(WORD_DIGIT.get(t.lower(), t) for t in TOKEN.findall(m.group()))


def mask_word_run(m: re.Match) -> str:
    """A run of 9+ spoken digits: masked if it is a phone number, else left exactly as typed."""
    digits = words_to_digits(m).translate(DEVANAGARI_DIGITS)
    return PHONE.sub(mask_phone, digits) if PHONE.search(digits) else m.group()


def mask_phone(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group())[-10:]
    return digits[:2] + "XXXXXX" + digits[-2:]


def mask_pii(text: str) -> str:
    """'call nau aath saat chhe paanch chaar teen do ek shunya' -> 'call 98XXXXXX10';
    'priya.k@gmail.com' -> 'p***@g***.com', 'H.No. 45B' -> 'H.No. XX'. Everything else stays
    exactly as the user wrote it, Devanagari digits included. Emails go first: they can hold
    digits."""
    text = EMAIL.sub(r"\1***@\2***.\3", text)
    text = RUN.sub(mask_word_run, text)
    # Look for phones in a copy with ASCII digits. One digit becomes one digit, so the copy
    # has the same length and its match positions point at the same spots in the original.
    ascii_copy = text.translate(DEVANAGARI_DIGITS)
    for m in reversed(list(PHONE.finditer(ascii_copy))):  # from the end, so spans don't shift
        text = text[:m.start()] + mask_phone(m) + text[m.end():]
    return HOUSE.sub(r"\1XX", text)


# ---------- encryption ----------


@cache
def fernet() -> Fernet:
    """One Fernet key from PII_KEY (ARCH §0). Locally an empty key is generated once into
    .cache/; anywhere else an empty key stops the app (called at startup)."""
    key = settings.pii_key.get_secret_value()
    if not key:
        if settings.app_env != "local":
            raise RuntimeError("PII_KEY must be set outside APP_ENV=local")
        key = local_secret("dev_pii.key", lambda: Fernet.generate_key().decode())
    return Fernet(key)


def encrypt(text: str) -> bytes:
    return fernet().encrypt(text.encode())


def decrypt(token: bytes) -> str:
    return fernet().decrypt(bytes(token)).decode()  # bytes(): psycopg returns memoryview
