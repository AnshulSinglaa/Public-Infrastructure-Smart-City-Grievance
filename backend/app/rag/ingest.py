"""Document corpus pipeline (HLD-S6 §3): sources.yaml -> fetch -> changed? -> extract -> chunk
-> embed -> Postgres + Qdrant. Runs in the ingest GitHub Action or on a laptop, never in the API.

Idempotent: an unchanged document (304, or the same cleaned-text hash) writes nothing new.
Postgres is the source of truth; Qdrant point id = chunk.id, and reconcile() makes Qdrant match.
"""

import hashlib
import io
import ipaddress
import math
import re
import socket
import time
import unicodedata
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.config import ROOT, settings

SOURCES = ROOT / "data" / "corpus" / "sources.yaml"
RAW_DIR = ROOT / ".cache" / "corpus"  # raw copies (audit trail), git-ignored
MANUAL_DIR = ROOT / "data" / "corpus" / "manual"  # browser downloads <key>.pdf (git-ignored)
COLLECTION, ALIAS = "rule_chunks_v2", "rule_chunks"
DENSE_DIM = settings.embed_dims  # 768, same as the query side
USER_AGENT = "Mozilla/5.0 (compatible; CivicCorpusBot/1.0; public documents only)"
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 300
DEAD_AFTER = 3  # failures in a row (404/410 = dead at once)
MAX_REDIRECTS = 5
LAYERS = {"national", "state", "delhi"}
LEVELS = {"central", "state", "local"}
# The crawler only talks to official hosts (REVIEW R-08): government domains, plus the few issuing
# bodies whose own site is not on one (state-licensed utilities, DMRC). Every redirect hop is
# re-checked against this list, must be https, and must not resolve to a private address.
ALLOWED_SUFFIXES = (".gov.in", ".nic.in", ".sansad.in")
ALLOWED_HOSTS = {"sansad.in", "www.iglonline.net", "delhimetrorail.com", "www.bsesdelhi.com",
                 "tatapower-ddl.com", "www.tatapower-ddl.com"}
DOC_TYPES = {"act", "rules", "notification", "gazette", "charter", "court_filing", "official_page",
             "parliament_answer", "govt_news"}
PAYLOAD_INDEXES = {"status": "keyword", "tier": "integer", "layer": "keyword",
                   "state_code": "keyword", "department_codes": "keyword", "categories": "keyword",
                   "lang": "keyword", "valid_to": "keyword", "doc_id": "integer",
                   "level": "keyword", "covers": "keyword", "not_for": "keyword"}
# Qdrant payload fields that come from sources.yaml (refreshed on every run, D-01).
DOC_FIELDS = ("tier", "layer", "state_code", "department_codes", "categories", "lang", "level",
              "covers", "not_for")


# ---------- source list ----------

def load_sources(path: Path = SOURCES) -> list[dict]:
    """Read and check sources.yaml. A bad entry stops the run (the file is reviewed in git)."""
    sources = yaml.safe_load(path.read_text(encoding="utf-8"))
    keys = set()
    for s in sources:
        assert s["key"] not in keys, f"duplicate key {s['key']}"
        keys.add(s["key"])
        assert official_url(s["url"]), f"{s['key']}: https + official host only"
        assert s["layer"] in LAYERS and s["doc_type"] in DOC_TYPES and s["tier"] in (1, 2), s["key"]
        assert s.get("level") in LEVELS, f"{s['key']}: level must be one of {LEVELS}"
        assert s["layer"] != "state" or s.get("state_code"), f"{s['key']}: state needs state_code"
    return sources


# ---------- text ----------

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalise(text: str) -> str:
    """The one text form used for hashing and quote checks: NFC, ASCII digits, single spaces."""
    text = unicodedata.normalize("NFC", text.replace("\0", "")).translate(DEVANAGARI_DIGITS)
    return re.sub(r"\s+", " ", text).strip()


def extract_pdf(data: bytes) -> list[tuple[int, str]]:
    """[(page number, text)] with pypdf. Pages with no text layer come back empty (a scan).
    Some PDFs come out as "e ither t hrough a co ntractor" in pypdf's default mode (B-070);
    for those pages the layout mode, which places letters by position, reads correctly."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages[:MAX_PAGES]):
        text = page.extract_text() or ""
        if broken_ratio(text) > 0.08:
            layout = page.extract_text(extraction_mode="layout") or ""
            if broken_ratio(layout) < broken_ratio(text):
                text = layout
        pages.append((i + 1, text))
    return pages


def broken_ratio(text: str) -> float:
    """Share of 1-2 letter word pieces that are not real short words: high = split-up words."""
    words = re.findall(r"[A-Za-z]+", text)
    if not words:
        return 0.0
    short = [w for w in words if len(w) <= 2 and w.lower() not in SHORT_WORDS]
    return len(short) / len(words)


SHORT_WORDS = {"a", "i", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it",
               "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we", "am", "mr", "ms",
               "dr", "st", "ii", "iv", "vi", "ix", "xi", "id", "oa", "ea", "ld", "cm", "km",
               "nh", "rs", "pg", "pm", "hq", "ji"}


def readable(text: str) -> bool:
    """Reject garbage chunks (broken font maps, split letters) before anyone sees them (B-070)."""
    if not text:
        return False
    # Letters, marks, digits, punctuation and spaces of any script count as readable; garbage is
    # control or private-use characters, replacement marks and stray symbols.
    ok = sum(unicodedata.category(ch)[0] in "LMNPZ" or ch in "₹%&+=" for ch in text)
    return ok / len(text) >= 0.9 and broken_ratio(text) <= 0.15


def extract_html(data: bytes, url: str) -> list[tuple[int | None, str]]:
    """Main text of a page with trafilatura (menus and footers dropped). One 'page': None."""
    import trafilatura

    text = trafilatura.extract(data, url=url, include_tables=True,
                               include_comments=False, favor_recall=True) or ""
    return [(None, text)]


def is_pdf(data: bytes, content_type: str) -> bool:
    return data[:5] == b"%PDF-" or "pdf" in content_type


# A line that starts a new section: "Section 4", "4.", "4.1", "(a)", "Chapter II", Hindi धारा/नियम.
HEADING = re.compile(r"^(section\s+\d+|chapter\s+[ivxlc\d]+|rule\s+\d+|\d+(\.\d+)*[.)]\s|"
                     r"\([a-z0-9]{1,4}\)\s|धारा\s*\d+|नियम\s*\d+)", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=[.?!।;])\s+")
MIN_WORDS, MAX_WORDS = 80, 450  # ~tokens; HLD: merge < 80, split > 450


@dataclass
class Chunk:
    text: str
    section_ref: str | None
    page: int | None
    ordinal: int = 0
    sha: str = field(default="")


def chunk_pages(pages: list[tuple[int | None, str]]) -> list[Chunk]:
    """Split at section headings, keep chunks between 80 and 450 words, cut at sentence ends,
    repeat the last sentence of a long section at the start of its next chunk (overlap)."""
    units = []  # (section_ref, page, sentence)
    section = None
    for page, text in pages:
        buf = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = HEADING.match(line)
            if m:
                units += [(section, page, s) for s in split_sentences(" ".join(buf))]
                # A short line is a title ("Section 4. Appeals"); a long one is a numbered
                # paragraph, so keep only its number ("4."), not a sentence fragment.
                short = len(line.split()) <= 10
                buf, section = [], normalise(line if short else m.group(0))[:80]
            buf.append(line)
        units += [(section, page, s) for s in split_sentences(" ".join(buf))]

    chunks, cur = [], []
    for unit in units:
        size = sum(len(u[2].split()) for u in cur)
        new_section = cur and unit[0] != cur[-1][0] and size >= MIN_WORDS
        if new_section or (cur and size + len(unit[2].split()) > MAX_WORDS):
            chunks.append(cur)
            cur = [] if new_section else [cur[-1]]  # one-sentence overlap inside a section
        cur.append(unit)
    if cur:
        chunks.append(cur)

    out = []
    for i, c in enumerate(chunks):
        text = " ".join(u[2] for u in c)
        out.append(Chunk(text, c[0][0] if c[0][0] else None, c[0][1], i,
                         hashlib.sha256(normalise(text).encode()).hexdigest()))
    return out


def split_sentences(text: str) -> list[str]:
    """Sentences, with any 'sentence' longer than MAX_WORDS (a table, a list) cut by words."""
    out = []
    for s in SENTENCE_END.split(normalise(text)):
        words = s.split()
        out += [" ".join(words[i:i + MAX_WORDS]) for i in range(0, len(words), MAX_WORDS)]
    return [s for s in out if s]


INJECTION = re.compile(r"ignore (all |any )?(previous|prior|above) instructions|system prompt|"
                       r"you are now|disregard (the|all|your)|<\s*/?\s*(script|system|doc)\b",
                       re.IGNORECASE)


# ---------- embeddings ----------

class GeminiEmbedder:
    """Dense: the gateway's embed() (settings.embed_model / embed_dims, re-normalised), the same
    function the query side uses, task_type RETRIEVAL_DOCUMENT. Sparse: fastembed Qdrant/bm25."""

    def __init__(self):
        from fastembed import SparseTextEmbedding

        self.bm25 = SparseTextEmbedding("Qdrant/bm25")

    def dense(self, texts: list[str]) -> list[list[float]]:
        from app.llm.gateway import ProviderDown, embed

        out = []
        # 20 texts per request keeps one call well under the free tier's tokens-per-minute.
        for i in range(0, len(texts), 20):
            for attempt in range(5):
                try:
                    out += embed(texts[i:i + 20], task_type="RETRIEVAL_DOCUMENT")
                    break
                except ProviderDown:
                    if attempt == 4:
                        raise
                    time.sleep(60)  # a free-tier 429: the per-minute window resets
        return out

    def sparse(self, texts: list[str]) -> list[qm.SparseVector]:
        return [qm.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
                for e in self.bm25.embed(texts)]


class FakeEmbedder:
    """Tests and CI: deterministic vectors from word hashes, no network, no model files."""

    def dense(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DENSE_DIM
            for w in t.lower().split():
                v[zlib.crc32(w.encode()) % DENSE_DIM] += 1
            out.append(unit(v))
        return out

    def sparse(self, texts: list[str]) -> list[qm.SparseVector]:
        out = []
        for t in texts:
            counts = {}
            for w in t.lower().split():
                k = zlib.crc32(w.encode()) % 100_000
                counts[k] = counts.get(k, 0) + 1.0
            out.append(qm.SparseVector(indices=list(counts), values=list(counts.values())))
        return out


def unit(v) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# ---------- Qdrant ----------

def qdrant() -> QdrantClient:
    key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return QdrantClient(url=settings.qdrant_url, api_key=key or None, timeout=30)


def ensure_collection(q: QdrantClient, name: str = COLLECTION, alias: str | None = ALIAS) -> None:
    """Named vectors `dense` (768, cosine) + sparse `bm25` (IDF), payload indexes, and the alias
    `rule_chunks` -> this collection. The API's old empty unnamed `rule_chunks` collection is
    replaced by the alias; a non-empty one stops the run (never delete data silently)."""
    if not q.collection_exists(name):
        q.create_collection(
            name, vectors_config={"dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"bm25": qm.SparseVectorParams(modifier=qm.Modifier.IDF)})
    have = q.get_collection(name).payload_schema or {}
    for f, kind in PAYLOAD_INDEXES.items():  # also adds indexes for fields added later
        if f not in have:
            q.create_payload_index(name, f, field_schema=kind)
    if not alias:
        return
    aliases = {a.alias_name: a.collection_name for a in q.get_aliases().aliases}
    if aliases.get(alias) == name:
        return
    if alias not in aliases and q.collection_exists(alias):
        if q.count(alias).count:
            raise RuntimeError(f"Qdrant collection {alias!r} has points; move them before aliasing")
        q.delete_collection(alias)
    q.update_collection_aliases(change_aliases_operations=[
        qm.CreateAliasOperation(create_alias=qm.CreateAlias(collection_name=name, alias_name=alias))])


# ---------- fetch ----------

def official_url(url: str) -> bool:
    """https and an allow-listed official host (no IP literals, no other ports)."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return (p.scheme == "https" and p.port in (None, 443) and not p.username
            and (host.endswith(ALLOWED_SUFFIXES) or host in ALLOWED_HOSTS))


def public_ips(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


class Blocked(Exception):
    """A URL or redirect the crawler refuses (off the allowlist, http, private address)."""


class Fetcher:
    """Polite, safe GET: generic User-Agent, timeouts, robots.txt, 1 request/s per host, 20 MB
    cap. Redirects are followed by hand (at most 5) and every hop is checked like the first URL:
    https, official host, and the host must not resolve to a private/loopback/link-local IP."""

    def __init__(self, client: httpx.Client | None = None, delay_s: float = 1.0, resolve=public_ips):
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=False,
            timeout=httpx.Timeout(30, connect=10))
        self.delay_s = delay_s
        self.resolve = resolve
        self.last: dict[str, float] = {}
        self.robots: dict[str, list[str]] = {}

    def check(self, url: str) -> None:
        if not official_url(url):
            raise Blocked("off_allowlist")
        try:
            ips = self.resolve(urlparse(url).hostname)
        except OSError as e:
            raise httpx.ConnectError(f"dns: {e}") from e
        for ip in ips:
            a = ipaddress.ip_address(ip)
            if not a.is_global or a.is_multicast:
                raise Blocked("private_address")

    def open(self, url: str, headers: dict):
        """-> (final url, streamed response). The caller closes the response."""
        for _ in range(MAX_REDIRECTS + 1):
            self.check(url)
            self.wait(urlparse(url).netloc)
            r = self.client.send(self.client.build_request("GET", url, headers=headers), stream=True)
            if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
                r.close()
                url = urljoin(url, r.headers["location"])
                continue
            return url, r
        raise Blocked("too_many_redirects")

    def wait(self, host: str) -> None:
        gap = time.monotonic() - self.last.get(host, 0)
        if gap < self.delay_s:
            time.sleep(self.delay_s - gap)
        self.last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        """Disallow lines for `User-agent: *` only (simple robots.txt reading)."""
        p = urlparse(url)
        if p.netloc not in self.robots:
            rules = []
            try:
                _, r = self.open(f"{p.scheme}://{p.netloc}/robots.txt", {})
                text = r.read()[:500_000].decode("utf-8", "replace") if r.status_code == 200 else ""
                r.close()
                if "text/plain" in r.headers.get("content-type", ""):
                    applies = False
                    for line in text.splitlines():
                        k, _, v = line.partition(":")
                        k, v = k.strip().lower(), v.split("#")[0].strip()
                        if k == "user-agent":
                            applies = v == "*"
                        elif k == "disallow" and applies and v:
                            rules.append(v)
            except (httpx.HTTPError, Blocked):
                pass  # no robots.txt reachable = allowed (the page itself is checked again)
            self.robots[p.netloc] = rules
        path = p.path or "/"
        return not any(path.startswith(rule) for rule in self.robots[p.netloc])

    def get(self, url: str, etag: str | None, last_modified: str | None):
        """-> (status, body bytes, headers). Body over 20 MB raises ValueError('too_large')."""
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        _, r = self.open(url, headers)
        try:
            body = bytearray()
            if r.status_code == 200:
                for part in r.iter_bytes():
                    body += part
                    if len(body) > MAX_BYTES:
                        raise ValueError("too_large")
            return r.status_code, bytes(body), r.headers
        finally:
            r.close()


# ---------- database ----------

UPSERT_DOC = """
INSERT INTO policy_doc (key, title, url, lang, issuer, doc_type, tier, layer, state_code, issued_on,
                        department_codes, categories, rules, recrawl_days, meta, level, covers,
                        not_for, superseded_note)
VALUES (%(key)s, %(title)s, %(url)s, %(lang)s, %(issuer)s, %(doc_type)s, %(tier)s, %(layer)s,
        %(state_code)s, %(issued_on)s, %(department_codes)s, %(categories)s, %(rules)s,
        %(recrawl_days)s, '{}', %(level)s, %(covers)s, %(not_for)s, %(superseded_note)s)
ON CONFLICT (key) DO UPDATE SET title = EXCLUDED.title, url = EXCLUDED.url, lang = EXCLUDED.lang,
    issuer = EXCLUDED.issuer, doc_type = EXCLUDED.doc_type, tier = EXCLUDED.tier,
    layer = EXCLUDED.layer, state_code = EXCLUDED.state_code, issued_on = EXCLUDED.issued_on,
    department_codes = EXCLUDED.department_codes, categories = EXCLUDED.categories,
    rules = EXCLUDED.rules, recrawl_days = EXCLUDED.recrawl_days, level = EXCLUDED.level,
    covers = EXCLUDED.covers, not_for = EXCLUDED.not_for, superseded_note = EXCLUDED.superseded_note,
    status = CASE WHEN policy_doc.status = 'retired' THEN 'active' ELSE policy_doc.status END
RETURNING id, etag, last_modified, last_checked_at, fail_count, status
"""


def sync_sources(conn, sources: list[dict]) -> dict[str, tuple]:
    """sources.yaml -> policy_doc rows (insert or update by key)."""
    rows = {}
    for s in sources:
        params = {"state_code": None, "issued_on": None, "lang": "en", "department_codes": [],
                  "categories": [], "rules": [], "recrawl_days": 30, "covers": [], "not_for": [],
                  "superseded_note": None, **s}
        rows[s["key"]] = conn.execute(UPSERT_DOC, params).fetchone()
    return rows


def retire_missing(conn, sources: list[dict]) -> int:
    """Sources removed from the full sources.yaml are marked retired (their rows stay) and their
    chunks leave search (REVIEW R-03); reconcile() then drops their Qdrant points."""
    ids = [r[0] for r in conn.execute(
        """UPDATE policy_doc SET status = 'retired'
           WHERE key IS NOT NULL AND key <> ALL(%s) AND status <> 'retired' RETURNING id""",
        ([s["key"] for s in sources],))]
    conn.execute("""UPDATE chunk SET status = 'superseded'
                    WHERE doc_id = ANY(%s) AND status IN ('pending_review', 'approved')""", (ids,))
    return len(ids)


def payload(doc: dict, doc_id: int, status: str) -> dict:
    """Qdrant filter fields only (contract §2.3); the text stays in Postgres."""
    return {"status": status, "valid_to": None, "doc_id": doc_id,
            "embed_model": settings.embed_model, **doc_payload(doc)}


def doc_payload(doc: dict) -> dict:
    defaults = {"state_code": None, "department_codes": [], "categories": [], "lang": "en",
                "covers": [], "not_for": []}
    return {f: doc.get(f, defaults.get(f)) for f in DOC_FIELDS}


def refresh_payloads(conn, q: QdrantClient, sources: list[dict], collection: str) -> None:
    """Copy each source's sources.yaml fields onto its points, so editing level / layer / covers
    for an unchanged document still reaches search. No re-embedding (REVIEW D-01)."""
    ids = dict(conn.execute("SELECT key, id FROM policy_doc WHERE key = ANY(%s)",
                            ([s["key"] for s in sources],)).fetchall())
    for s in sources:
        if s["key"] in ids:
            q.set_payload(collection, doc_payload(s), points=qm.Filter(must=[qm.FieldCondition(
                key="doc_id", match=qm.MatchValue(value=ids[s["key"]]))]))


# ---------- one document ----------

@dataclass
class Result:
    key: str
    outcome: str  # new | changed | unchanged | failed | dead | needs_ocr | skipped
    detail: str = ""
    chunks: int = 0


def ingest_one(conn, q: QdrantClient, fetcher: Fetcher, embedder, doc: dict, row: tuple,
               collection: str = COLLECTION) -> Result:
    doc_id, etag, last_modified, _, fail_count, _ = row
    key, url = doc["key"], doc["url"]

    def fail(reason: str, dead: bool = False) -> Result:
        n = fail_count + 1
        dead = dead or n >= DEAD_AFTER
        conn.execute("""UPDATE policy_doc SET fail_count = %s, last_error = %s, last_checked_at = now(),
                        status = CASE WHEN %s THEN 'dead' ELSE status END WHERE id = %s""",
                     (n, reason, dead, doc_id))
        return Result(key, "dead" if dead else "failed", reason)

    def checked_ok(error: str | None = None, headers=None) -> None:
        """Fetched fine. ETag/Last-Modified are saved only once the text is stored (headers
        given), so a run that crashes half way does not get a 304 for a document it never kept."""
        conn.execute("""UPDATE policy_doc SET fail_count = 0, last_error = %s, status = 'active',
                        last_checked_at = now(), last_ok_at = now() WHERE id = %s""", (error, doc_id))
        if headers is not None:
            conn.execute("UPDATE policy_doc SET etag = %s, last_modified = %s WHERE id = %s",
                         (headers.get("etag"), headers.get("last-modified"), doc_id))

    manual = next((f for f in (MANUAL_DIR / f"{key}.pdf", MANUAL_DIR / f"{key}.html") if f.exists()),
                  None)
    if manual:  # the user saved it from the official URL in a browser (B-061)
        status, body, headers = 200, manual.read_bytes(), {}
    else:
        hint = " (download it in a browser, see manual: in sources.yaml)" if doc.get("manual") else ""
        try:
            if not fetcher.allowed(url):
                return fail("robots_disallowed")
            status, body, headers = fetcher.get(url, etag, last_modified)
        except Blocked as e:
            return fail(f"blocked_{e}")
        except ValueError as e:
            return fail(str(e))
        except httpx.HTTPError as e:
            return fail(type(e).__name__ + hint)
        if status == 403 and hint:
            return fail("http_403" + hint)
    if status == 304:
        checked_ok()
        return Result(key, "unchanged", "304")
    if status in (404, 410):
        return fail(f"http_{status}", dead=True)
    if status != 200:
        return fail(f"http_{status}")

    ctype = headers.get("content-type", "")
    try:
        pages = extract_pdf(body) if is_pdf(body, ctype) else extract_html(body, url)
    except Exception as e:  # noqa: BLE001 - a broken file must not stop the run
        return fail(f"extract_error:{type(e).__name__}")
    full = normalise(" ".join(t for _, t in pages))
    if len(full) < 200:
        # ponytail: no OCR yet (HLD §3.4: Tesseract only for a scan we really need); flag it.
        reason = "needs_ocr" if is_pdf(body, ctype) else "no_text"
        checked_ok(reason)
        return Result(key, reason, f"{len(full)} chars")

    sha = hashlib.sha256(full.encode()).hexdigest()
    prev = conn.execute("""SELECT id, version_no, sha256, status FROM doc_version WHERE doc_id = %s
                           ORDER BY version_no DESC LIMIT 1""", (doc_id,)).fetchone()
    if prev and prev[2] == sha:
        checked_ok(headers=headers)
        return Result(key, "unchanged", "same hash")

    all_chunks = chunk_pages(pages)
    chunks = [c for c in all_chunks if readable(c.text)]  # garbage text never reaches users
    for i, c in enumerate(chunks):
        c.ordinal = i
    dropped = len(all_chunks) - len(chunks)
    if not chunks:
        checked_ok("unreadable")
        return Result(key, "no_text", f"{dropped} unreadable chunks")
    flagged = any(INJECTION.search(c.text) for c in chunks)
    version_no = prev[1] + 1 if prev else 1
    raw_key = save_raw(key, version_no, body, ".pdf" if is_pdf(body, ctype) else ".html")
    same_as_approved = conn.execute(
        "SELECT 1 FROM doc_version WHERE doc_id = %s AND sha256 = %s AND status IN "
        "('approved', 'superseded') AND reviewed_at IS NOT NULL", (doc_id, sha)).fetchone()

    # 1. Postgres, one transaction: the version and its chunks.
    with conn.transaction():
        new_status = "pending_review"  # a new source always goes to review (HLD §3.6)
        if same_as_approved and not flagged:
            new_status = "approved"  # the same text was approved before
        elif prev and prev[3] == "approved" and not flagged and quotes_still_there(conn, prev[0],
                                                                                  chunks):
            new_status = "approved"  # every approved quote is still there (HLD §3.6)
        vid = conn.execute("""INSERT INTO doc_version (doc_id, version_no, sha256, raw_key, status)
                              VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                           (doc_id, version_no, sha, raw_key, new_status)).fetchone()[0]
        ids = []
        for c in chunks:
            ids.append(conn.execute(
                """INSERT INTO chunk (doc_id, doc_version_id, ordinal, section_ref, page, text,
                                      text_norm, lang, text_sha, injection_flag, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (doc_id, vid, c.ordinal, c.section_ref, c.page, c.text, normalise(c.text),
                 doc.get("lang", "en"), c.sha, bool(INJECTION.search(c.text)), new_status),
            ).fetchone()[0])
        old_ids = []
        # The previous version leaves search when the new one is approved, or when nobody had
        # approved it anyway. An approved one keeps serving while the new one waits for review.
        retire_prev = bool(prev) and (new_status == "approved" or prev[3] == "pending_review")
        if prev:
            old_ids = [r[0] for r in conn.execute(
                "SELECT id FROM chunk WHERE doc_version_id = %s", (prev[0],))]
            if new_status == "approved":
                repoint_citations(conn, prev[0], vid)
            if retire_prev:
                conn.execute("""UPDATE doc_version SET status = 'superseded', valid_to = now()
                                WHERE id = %s""", (prev[0],))
                conn.execute("UPDATE chunk SET status = 'superseded' WHERE doc_version_id = %s",
                             (prev[0],))
        checked_ok(headers=headers)

    # 2. Qdrant: vectors for the new chunks. An unchanged chunk (same text_sha as before)
    # copies its old vectors instead of a new embedding call.
    upsert_vectors(q, embedder, conn, doc, doc_id, list(zip(ids, chunks)), new_status, collection,
                   reuse_from=old_ids)
    if retire_prev and old_ids:
        q.delete(collection, points_selector=qm.PointIdsList(points=old_ids))
    detail = new_status + (f", {dropped} unreadable chunks dropped" if dropped else "")
    return Result(key, "changed" if prev else "new", detail, len(chunks))


def save_raw(key: str, version_no: int, body: bytes, ext: str) -> str:
    """Keep the raw file: corpus/<key>/<version>.<ext>. Locally under .cache/; in the Action
    the folder is uploaded as a build artifact. ponytail: Supabase Storage bucket upload later."""
    rel = f"corpus/{key}/{version_no}{ext}"
    path = RAW_DIR.parent / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return rel


def quotes_still_there(conn, old_version_id: int, chunks: list[Chunk]) -> bool:
    """New version of an approved doc: is every approved quote still in the new text?
    Missing ones become needs_review (new dossiers then show 'No official document found')."""
    new_text = " ".join(normalise(c.text) for c in chunks)
    rows = conn.execute("""SELECT ci.id, ci.quoted_span FROM citation ci JOIN chunk ch ON ch.id = ci.chunk_id
                           WHERE ch.doc_version_id = %s AND ci.status = 'approved'""",
                        (old_version_id,)).fetchall()
    missing = [cid for cid, quote in rows if normalise(quote) not in new_text]
    if missing:
        conn.execute("UPDATE citation SET status = 'needs_review' WHERE id = ANY(%s)", (missing,))
    return not missing


def repoint_citations(conn, old_version_id: int, new_version_id: int) -> None:
    """Approved citations move to the first new chunk that holds the same quote."""
    new = conn.execute("SELECT id, text_norm FROM chunk WHERE doc_version_id = %s ORDER BY ordinal",
                       (new_version_id,)).fetchall()
    cites = conn.execute("""SELECT ci.id, ci.quoted_span FROM citation ci JOIN chunk ch ON ch.id = ci.chunk_id
                            WHERE ch.doc_version_id = %s AND ci.status = 'approved'""",
                         (old_version_id,)).fetchall()
    for cid, quote in cites:
        target = next((nid for nid, text in new if normalise(quote) in text), None)
        if target:
            conn.execute("UPDATE citation SET chunk_id = %s WHERE id = %s", (target, cid))


def upsert_vectors(q, embedder, conn, doc, doc_id, items, status, collection, reuse_from=()):
    """items: [(chunk_id, Chunk)]. Point id = chunk.id."""
    reuse = {}
    if reuse_from:
        sha_by_id = dict(conn.execute("SELECT id, text_sha FROM chunk WHERE id = ANY(%s)",
                                      (list(reuse_from),)).fetchall())
        for p in q.retrieve(collection, ids=list(reuse_from), with_vectors=True):
            reuse[sha_by_id.get(p.id)] = p.vector
    todo = [(cid, c) for cid, c in items if c.sha not in reuse]
    dense = embedder.dense([c.text for _, c in todo]) if todo else []
    sparse = embedder.sparse([c.text for _, c in todo]) if todo else []
    vecs = {cid: {"dense": d, "bm25": s} for (cid, _), d, s in zip(todo, dense, sparse)}
    points = [qm.PointStruct(id=cid, vector=vecs.get(cid) or reuse[c.sha],
                             payload=payload(doc, doc_id, status)) for cid, c in items]
    for i in range(0, len(points), 64):
        q.upsert(collection, points[i:i + 64])


def reconcile(conn, q: QdrantClient, embedder, sources: list[dict],
              collection: str = COLLECTION) -> tuple[int, int]:
    """Postgres wins: live chunks (pending_review/approved) missing from Qdrant are embedded,
    Qdrant points with no live chunk are deleted. -> (added, removed)."""
    by_key = {s["key"]: s for s in sources}
    live = {r[0]: r for r in conn.execute("""
        SELECT c.id, c.doc_id, c.text, c.text_sha, c.status, d.key FROM chunk c
        JOIN policy_doc d ON d.id = c.doc_id
        WHERE c.status IN ('pending_review', 'approved') AND d.key IS NOT NULL
          AND d.status <> 'retired'""")}
    have, offset = set(), None
    while True:
        pts, offset = q.scroll(collection, limit=1000, offset=offset, with_payload=False)
        have |= {p.id for p in pts}
        if offset is None:
            break
    extra = list(have - set(live))
    if extra:
        q.delete(collection, points_selector=qm.PointIdsList(points=extra))
    missing = [live[i] for i in set(live) - have if live[i][5] in by_key]
    for cid, doc_id, text, sha, status, key in missing:
        upsert_vectors(q, embedder, conn, by_key[key], doc_id,
                       [(cid, Chunk(text, None, None, 0, sha))], status, collection)
    return len(missing), len(extra)


def due(row: tuple, recrawl_days: int) -> bool:
    last_checked = row[3]
    return last_checked is None or (date.today() - last_checked.date()).days >= recrawl_days


def run(conn, sources: list[dict], embedder, fetcher: Fetcher | None = None,
        q: QdrantClient | None = None, collection: str = COLLECTION, alias: str | None = ALIAS,
        only: str | None = None, force: bool = False, refetch: bool = False,
        log=print) -> list[Result]:
    """The whole job. conn should be autocommit, so each document is saved on its own.
    force = ignore recrawl_days; refetch = also ignore ETags (after an extraction change)."""
    q = q or qdrant()
    fetcher = fetcher or Fetcher()
    ensure_collection(q, collection, alias)
    rows = sync_sources(conn, sources)
    results = []
    for doc in sources:
        row = rows[doc["key"]]
        if refetch:
            row = (row[0], None, None, *row[3:])
        if (only and doc["key"] != only) or (not force and not only
                                             and not due(row, doc.get("recrawl_days", 30))):
            continue
        try:
            r = ingest_one(conn, q, fetcher, embedder, doc, row, collection)
        except Exception as e:  # noqa: BLE001 - one bad document must not stop the job
            r = Result(doc["key"], "failed", f"error:{type(e).__name__}: {str(e)[:120]}")
        log(f"{r.outcome:10} {r.key:40} {r.chunks:4} {r.detail}")
        results.append(r)
    refresh_payloads(conn, q, sources, collection)
    added, removed = reconcile(conn, q, embedder, sources, collection)
    log(f"reconcile: {added} added to Qdrant, {removed} removed")
    return results
