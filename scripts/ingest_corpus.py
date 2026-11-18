"""Build/refresh the official-document corpus (HLD-S6 §3) from data/corpus/sources.yaml.

    uv run python scripts/ingest_corpus.py              # due sources (not checked in recrawl_days)
    uv run python scripts/ingest_corpus.py --all        # every source, even if checked recently
    uv run python scripts/ingest_corpus.py --only djb-grievance
    uv run python scripts/ingest_corpus.py --all --refetch   # after an extraction change

Uses DATABASE_URL, QDRANT_URL, QDRANT_API_KEY, GOOGLE_API_KEY from the environment / .env.
Safe to re-run: unchanged documents write nothing new. Prints a summary (also the Action summary).
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import connect
from app.rag import ingest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="ignore recrawl_days")
    p.add_argument("--only", help="one source key")
    p.add_argument("--refetch", action="store_true",
                   help="ignore ETags: download again (after an extraction change)")
    a = p.parse_args()

    sources = ingest.load_sources()
    embedder = ingest.GeminiEmbedder()  # tests use ingest.FakeEmbedder (no network)
    with connect(timeout=10) as conn:
        conn.autocommit = True  # each document is saved on its own
        results = ingest.run(conn, sources, embedder, only=a.only, force=a.all,
                             refetch=a.refetch)
        if not a.only:
            ingest.retire_missing(conn, sources)
        totals = conn.execute("""
            SELECT d.layer, count(DISTINCT d.id) FILTER (WHERE v.id IS NOT NULL), count(c.id)
            FROM policy_doc d
            LEFT JOIN doc_version v ON v.doc_id = d.id AND v.status <> 'superseded'
            LEFT JOIN chunk c ON c.doc_version_id = v.id
            WHERE d.status <> 'retired' AND d.key IS NOT NULL GROUP BY d.layer ORDER BY 1""").fetchall()
        # Documents nobody has approved yet: the review list (scripts/review_corpus.py).
        never_approved = [k for (k,) in conn.execute("""
            SELECT d.key FROM policy_doc d WHERE d.status <> 'retired' AND d.key IS NOT NULL
              AND EXISTS (SELECT 1 FROM doc_version v WHERE v.doc_id = d.id)
              AND NOT EXISTS (SELECT 1 FROM doc_version v WHERE v.doc_id = d.id
                              AND v.status = 'approved') ORDER BY 1""")]

    counts = Counter(r.outcome for r in results)
    lines = ["## Corpus ingest", "",
             "This run: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), "",
             "| layer | documents with text | chunks |", "|---|---|---|"]
    lines += [f"| {layer} | {docs} | {chunks} |" for layer, docs, chunks in totals]
    problems = [r for r in results if r.outcome in ("failed", "dead", "needs_ocr", "no_text")]
    if problems:
        lines += ["", "| source | outcome | why |", "|---|---|---|"]
        lines += [f"| {r.key} | {r.outcome} | {r.detail} |" for r in problems]
    if never_approved:
        lines += ["", f"New, waiting for your approval ({len(never_approved)}): "
                  + ", ".join(never_approved)]
    stored = {r.key for r in results if r.outcome in ("new", "changed", "unchanged")}
    manual = [s for s in sources if s.get("manual") and s["key"] not in stored]
    if manual:
        lines += ["", "Download these in a browser and save as data/corpus/manual/<key>.pdf:", ""]
        lines += [f"- `{s['key']}.pdf` <- {s['url']}" for s in manual]
    summary = "\n".join(lines)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary + "\n")


if __name__ == "__main__":
    main()
