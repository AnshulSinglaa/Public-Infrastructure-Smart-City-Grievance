"""Review the corpus on your laptop (HLD-S6 §3.6). Nothing is citable until you approve it.

    uv run python scripts/review_corpus.py list                     # versions waiting for review
    uv run python scripts/review_corpus.py show djb-grievance       # its chunks (first 300 chars)
    uv run python scripts/review_corpus.py approve djb-grievance    # latest version -> approved
    uv run python scripts/review_corpus.py reject djb-grievance
    uv run python scripts/review_corpus.py cite EM-03 <chunk_id> "exact words from the chunk" [deadline]

Approving a version makes its chunks searchable (Qdrant payload status=approved) and
supersedes the version before it. `cite` adds an approved rule citation only if the quote is
really in that chunk (cite-or-drop).
ponytail: no "top 3 proposed quotes per rule" yet; add once retrieval (rag/retrieve.py) exists.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from qdrant_client import models as qm

from app.db import connect
from app.rag import ingest

LATEST = """SELECT v.id, v.version_no, v.status, d.id FROM doc_version v JOIN policy_doc d ON d.id = v.doc_id
            WHERE d.key = %s ORDER BY v.version_no DESC LIMIT 1"""


def set_status(conn, key: str, status: str) -> None:
    row = conn.execute(LATEST, (key,)).fetchone()
    if not row:
        sys.exit(f"no version for {key}")
    vid, _, _, doc_id = row
    old_ids = []
    with conn.transaction():
        if status == "approved":  # the previous approved version leaves search
            old = conn.execute("""UPDATE doc_version SET status = 'superseded', valid_to = now()
                                  WHERE doc_id = %s AND id <> %s AND status = 'approved' RETURNING id""",
                               (doc_id, vid)).fetchall()
            for (oid,) in old:
                ingest.repoint_citations(conn, oid, vid)
                old_ids += [r[0] for r in conn.execute(
                    "UPDATE chunk SET status = 'superseded' WHERE doc_version_id = %s RETURNING id",
                    (oid,))]
        conn.execute("UPDATE doc_version SET status = %s, reviewed_at = now() WHERE id = %s",
                     (status, vid))
        ids = [r[0] for r in conn.execute(
            "UPDATE chunk SET status = %s WHERE doc_version_id = %s RETURNING id", (status, vid))]
    q = ingest.qdrant()
    if old_ids:  # the replaced version leaves search now, not at the next ingest (REVIEW D-02)
        q.delete(ingest.COLLECTION, points_selector=qm.PointIdsList(points=old_ids))
    if status == "approved":
        q.set_payload(ingest.COLLECTION, {"status": status}, points=ids)
    else:
        q.delete(ingest.COLLECTION, points_selector=qm.PointIdsList(points=ids))
    print(f"{key}: version {row[1]} {status} ({len(ids)} chunks)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["list", "show", "approve", "reject", "cite"])
    p.add_argument("args", nargs="*")
    a = p.parse_args()
    with connect(timeout=10) as conn:
        if a.cmd == "list":
            for row in conn.execute("""
                SELECT d.key, d.layer, v.version_no, count(c.id), bool_or(c.injection_flag), d.url
                FROM doc_version v JOIN policy_doc d ON d.id = v.doc_id
                LEFT JOIN chunk c ON c.doc_version_id = v.id
                WHERE v.status = 'pending_review' GROUP BY 1, 2, 3, 6 ORDER BY 2, 1"""):
                flag = "  INJECTION?" if row[4] else ""
                print(f"{row[0]:40} {row[1]:8} v{row[2]} {row[3]:4} chunks{flag}  {row[5]}")
        elif a.cmd == "show":
            for cid, sec, page, text, flag in conn.execute("""
                SELECT c.id, c.section_ref, c.page, c.text, c.injection_flag FROM chunk c
                WHERE c.doc_version_id = (SELECT v.id FROM doc_version v JOIN policy_doc d ON d.id = v.doc_id
                                          WHERE d.key = %s ORDER BY v.version_no DESC LIMIT 1)
                ORDER BY c.ordinal""", (a.args[0],)):
                print(f"[{cid}] p{page} {sec or ''}{' INJECTION?' if flag else ''}\n  {text[:300]}\n")
        elif a.cmd in ("approve", "reject"):
            set_status(conn, a.args[0], "approved" if a.cmd == "approve" else "rejected")
        else:
            rule_id, chunk_id, quote = a.args[0], int(a.args[1]), a.args[2]
            row = conn.execute("SELECT text_norm, status FROM chunk WHERE id = %s", (chunk_id,)).fetchone()
            if not row or ingest.normalise(quote) not in row[0]:
                sys.exit("quote is not in that chunk: not saved")
            if row[1] != "approved":
                sys.exit("approve the document first")
            conn.execute("""INSERT INTO citation (chunk_id, quoted_span, rule_id, role, status, reviewed_at)
                            VALUES (%s, %s, %s, %s, 'approved', now())""",
                         (chunk_id, quote, rule_id, "deadline" if a.args[3:] == ["deadline"] else "ownership"))
            conn.commit()
            print(f"{rule_id}: citation saved")


if __name__ == "__main__":
    main()
