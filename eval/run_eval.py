"""Q&A evaluation (PLAN §10.3-10.5, FIX-PLAN-S6 "Evaluation upgrades").

    uv run python eval/run_eval.py replay [--recording FILE] [--check]
        No network, no DB: scores a recording (default: the newest live-*, else fake-*) and
        writes eval/report.md. This is what CI runs.
    uv run python eval/run_eval.py fake [--only id,id]
        In-process app, PROVIDER_MODE=fake, local DB: records eval/recordings/fake-*.jsonl.
        Exercises guards, intent rules, location, layers, limits; answers are always not_found.
    uv run python eval/run_eval.py live [--pace 20] [--only id,id]
        In-process app, real Groq/Gemini, throttled (one model-bound row every --pace s, so
        30 answer rows take ~10 min = the free-tier survival check).
        Records eval/recordings/live-*.jsonl, then scores it like replay.
    uv run --project eval python eval/run_eval.py judge [--limit 20] [--recording FILE]
        The eval's own environment (eval/pyproject.toml: ragas, deepeval): Ragas + DeepEval on
        at most --limit answered rows of a live recording (judge: a Groq model other than the
        generator), written back into the recording, then scored.
fake / live import the app, so they run in the root project; replay runs in either.

Each row of eval/qa_golden.jsonl runs as a new user (no sticky state between rows), except
follow-ups (`followup_of`), which reuse the earlier row's user / report. /ask rows get a fresh
report made with the fake intake (no live tokens spent on intake).
"""

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
GOLDEN = EVAL / "qa_golden.jsonl"
RECORDINGS = EVAL / "recordings"
REPORT = EVAL / "report.md"
JUDGE_MODEL = "qwen/qwen3.8-27b"  # a different family from the generator (gpt-oss-120b)
FIXTURES = {  # /ask report fixtures: pin + text (intake is always the fake model)
    "pwd": (28.571496, 77.256778, "sadak pe bada gaddha hai"),       # MG Marg -> PWD dossier
    "jaipur": (26.9124, 75.7873, "road pe gaddha hai"),               # out_of_coverage, RJ
    "mumbai": (19.0760, 72.8777, "road pe gaddha hai"),               # out_of_coverage, MH
}
FIXED_STATUSES = {"fixed_reply", "off_topic", "blocked", "action", "needs_location"}
ACCEPT = {"answer": {"answered"}, "refuse": {"out_of_scope", "not_found"},
          "off_topic": {"off_topic"}, "blocked": {"blocked"}, "fixed_reply": {"fixed_reply"},
          "action": {"action"}, "needs_location": {"needs_location"}}
DEVANAGARI = re.compile("[ऀ-ॿ]")
LETTER = re.compile(r"[^\W\d_]")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
HINGLISH = {"hai", "hain", "kya", "mein", "ke", "ki", "ka", "karein", "kare", "aap", "se", "par",
            "kahan", "kaise", "nahi", "ko", "jaata", "jaati", "hota", "hoti", "liye", "apni"}


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]


# ====================================================================== scoring (pure)

def lang_of(text: str) -> str:
    letters = LETTER.findall(text)
    if not letters:
        return "none"
    if len(DEVANAGARI.findall(text)) / len(letters) > 0.5:
        return "hi"
    hits = sum(w in HINGLISH for w in re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if hits >= 2 else "en"


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def channel_text(body: dict) -> str:
    return " ".join(f'{w.get("label", "")} {w.get("value", "")}'
                    for w in body.get("where_to_ask") or [])


def answer_text(body: dict) -> str:
    return " ".join(s["text"] for s in body.get("sentences") or [])


def score_row(row: dict, rec: dict, mode: str) -> dict:
    """{check: True/False/None (n/a)} for one recorded row."""
    body, status = rec.get("body") or {}, (rec.get("body") or {}).get("status")
    c: dict[str, bool | None] = {}
    c["http_ok"] = rec.get("http") == 200
    if rec.get("http") == 503:
        c["no_503"] = False
    if not c["http_ok"]:
        return c
    accept = ACCEPT[row["expect"]]
    if row["expect"] == "answer" and mode == "fake":
        accept = {"answered", "not_found"}  # the fake model never answers
    c["expect"] = status in accept
    if row["expected_intent"] is not None or row["expect"] == "blocked":
        c["intent"] = body.get("intent") == row["expected_intent"]
    kinds = {a.get("kind") for a in body.get("actions") or []}
    if row.get("actions"):
        c["actions"] = set(row["actions"]) <= kinds
    reached_model = status in ("answered", "not_found", "out_of_scope")
    if status in FIXED_STATUSES:
        c["zero_answer_tokens"] = not any(u["task"] == "qa_answer" for u in rec.get("usage", []))
    if reached_model and row.get("expected_layer"):
        c["layer"] = body.get("layer") == row["expected_layer"]
    if reached_model and row.get("lru") is not None:
        c["local_rule_unknown"] = bool(body.get("local_rule_unknown")) == row["lru"]
    if reached_model and row["expect"] == "answer":
        shown = (answer_text(body) + " " + channel_text(body)).casefold()
        if row["forbidden_channels"]:
            c["channel"] = not any(ch.casefold() in shown for ch in row["forbidden_channels"])
        if row["must_not_contain"]:
            c["must_not_contain"] = not any(m.casefold() in shown for m in row["must_not_contain"])
    if status == "answered":
        text = answer_text(body)
        srcs = {s["id"]: s for s in body.get("sources") or []}
        c["citation_exact"] = all(rec.get("quote_in_chunk", {}).get(s["id"], True)
                                  for s in srcs.values() if s.get("kind") == "document")
        c["numbers_in_shown_quotes"] = all(
            numbers(s["text"]) <= set().union(*[numbers(srcs[i]["quote"]) for i in s["cites"]
                                                if i in srcs] or [set()])
            for s in body["sentences"])
        if not row["numbers_allowed"]:
            c["no_irrelevant_numbers"] = not numbers(text)
        want, got = row["lang"], lang_of(text)
        c["language"] = got == want if want != "hinglish" else got in ("hinglish", "en")
        if row.get("authority"):
            c["authority"] = any(n.casefold() in text.casefold()
                                 for n in row["authority"]["names_any"])
        if row["allowed_channels"]:
            both = (text + " " + channel_text(body)).casefold()
            c["channel_offered"] = any(ch.casefold() in both for ch in row["allowed_channels"])
        if row["must_include_any"]:
            c["must_include"] = any(m.casefold() in text.casefold() for m in row["must_include_any"])
        if row.get("followup_of"):
            c["followup_memory"] = c.get("layer", True) and c.get("channel", True) and \
                c.get("authority", True) is not False
    return c


def pct(ok: int, n: int) -> str:
    return f"{ok}/{n} = {ok / n:.2f}" if n else "n/a"


def summarize(golden: list[dict], recs: dict[str, dict], meta: dict) -> tuple[str, bool]:
    mode = meta.get("mode", "?")
    rows = [r for r in golden if r["id"] in recs]
    scored = {r["id"]: score_row(r, recs[r["id"]], mode) for r in rows}

    def rate(check: str, pred=lambda r: True):
        vals = [scored[r["id"]][check] for r in rows
                if pred(r) and scored[r["id"]].get(check) is not None]
        return sum(vals), len(vals)

    def intent_rate(label=None):
        return rate("intent", lambda r: label is None or r["expected_intent"] == label)

    civic = [r for r in rows if r["expected_intent"] == "general_civic"]
    civic_off = sum(recs[r["id"]].get("body", {}).get("intent") == "off_domain" for r in civic)
    answer_rows = [r for r in rows if r["expect"] == "answer"]
    false_refusals = sum(recs[r["id"]].get("body", {}).get("status") in
                         ("off_topic", "out_of_scope") for r in answer_rows)
    lat = sorted(recs[r["id"]]["latency_s"] for r in rows if recs[r["id"]].get("body", {})
                 .get("status") in ("answered", "not_found", "out_of_scope"))
    tok = [sum(u["input"] + u["output"] for u in recs[r["id"]].get("usage", [])
               if u["task"] == "qa_answer") for r in rows
           if any(u["task"] == "qa_answer" for u in recs[r["id"]].get("usage", []))]
    kept = sum(len(recs[r["id"]].get("body", {}).get("sentences") or []) for r in rows)
    dropped = sum(len(recs[r["id"]].get("dropped") or []) for r in rows)
    n503 = sum(recs[r["id"]].get("http") == 503 for r in rows)
    judged = [recs[r["id"]]["judge"] for r in rows if recs[r["id"]].get("judge")]

    def jmean(key):
        vals = [j[key] for j in judged if j.get(key) is not None]
        return (statistics.mean(vals), len(vals)) if vals else (None, 0)

    lines, gate = [], True

    def metric(name, value_str, ok, target, gating=True):
        nonlocal gate
        mark = "n/a" if ok is None else ("PASS" if ok else "**FAIL**")
        if ok is False and gating:
            gate = False
        lines.append(f"| {name} | {value_str} | {target} | {mark} |")

    def ratio_metric(name, okn, target_ratio, target, gating=True):
        ok, n = okn
        metric(name, pct(ok, n), (ok / n >= target_ratio) if n else None, target, gating)

    answers_real = mode != "fake"
    ratio_metric("Citation exactness (shown quote is in its chunk)", rate("citation_exact"), 1.0, "100 %")
    ratio_metric("Numbers in the shown quotes", rate("numbers_in_shown_quotes"), 1.0, "100 %")
    ratio_metric("No irrelevant numbers", rate("no_irrelevant_numbers"), 1.0, "100 %")
    ratio_metric("Correct layer", rate("layer"), 1.0, "100 %")
    ratio_metric("Local rule unknown flag", rate("local_rule_unknown"), 1.0, "100 %")
    ratio_metric("Refuse / off-topic / blocked / fixed rows",
                 rate("expect", lambda r: r["expect"] != "answer"), 1.0, "100 %")
    ratio_metric("Fixed replies with 0 answer-model tokens", rate("zero_answer_tokens"), 1.0, "100 %")
    metric("False refusal / false off-topic on answer rows", pct(false_refusals, len(answer_rows)),
           (false_refusals / len(answer_rows) <= 0.10) if answer_rows else None, "≤ 10 %")
    ratio_metric("Intent accuracy (all)", intent_rate(), 0.90, "≥ 0.90")
    ratio_metric("Intent: off_domain", intent_rate("off_domain"), 0.95, "≥ 0.95")
    ratio_metric("Intent: greeting", intent_rate("greeting"), 0.95, "≥ 0.95")
    metric("Civic question labelled off_domain", pct(civic_off, len(civic)),
           (civic_off / len(civic) <= 0.05) if civic else None, "≤ 5 %")
    ratio_metric("Right channel (no CPGRAMS for state/local)", rate("channel"), 1.0, "100 %")
    ratio_metric("Must-not-contain (PII, wrong timelines, Delhi rules)",
                 rate("must_not_contain"), 1.0, "100 %")
    ratio_metric("Actions offered (Start a report / pick)", rate("actions"), 1.0, "100 %")
    ratio_metric("Right authority named", rate("authority"), 0.90, "≥ 0.90")
    ratio_metric("Right channel offered", rate("channel_offered"), 0.90, "≥ 0.90")
    ratio_metric("Language and script match", rate("language"), 1.0, "100 %")
    ratio_metric("Follow-up memory", rate("followup_memory"), 1.0, "100 %")
    ratio_metric("Answer rows answered (not not_found)",
                 rate("expect", lambda r: r["expect"] == "answer"), 0.80, "≥ 0.80",
                 gating=answers_real)
    metric("Cite-or-drop pass rate (kept / model sentences)",
           pct(kept, kept + dropped), None if not kept + dropped else True, "info", gating=False)
    for key, name, target in (("ragas_faithfulness", "Ragas faithfulness", 0.90),
                              ("ragas_answer_relevancy", "Ragas answer relevancy", 0.80),
                              ("geval_need", "DeepEval GEval: addresses the user's need", 0.80),
                              ("geval_authority", "DeepEval GEval: right authority + channel", 0.80)):
        m, n = jmean(key)
        metric(name, f"{m:.2f} (n={n})" if n else "not judged", (m >= target) if n else None,
               f"≥ {target:.2f}")
    p50 = statistics.median(lat) if lat else None
    p95 = lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))] if lat else None
    timed = answers_real and lat
    metric("Latency p50 (model-bound rows)", f"{p50:.1f} s" if lat else "n/a",
           (p50 <= 3.0) if timed else None, "≤ 3 s")
    metric("Latency p95", f"{p95:.1f} s" if lat else "n/a", (p95 <= 7.0) if timed else None, "≤ 7 s")
    metric("Tokens per answer (input + output)", f"{statistics.mean(tok):.0f} (n={len(tok)})"
           if tok else "n/a", None, "info", gating=False)
    surv = meta.get("survival")
    metric("Free-tier survival (≥ 30 model rows / 10 min, no 503)",
           f'{surv["rows"]} rows in {surv["minutes"]:.1f} min, {n503} × 503' if surv else
           f"{n503} × 503 (not a paced live run)",
           (surv["rows"] >= 30 and surv["rows"] / max(surv["minutes"], 1e-9) >= 3 and n503 == 0)
           if surv else None, "30 / 10 min, 0 × 503")

    head = [f"# Q&A eval report ({meta.get('started', '?')}, mode **{mode}**)", "",
            (f"Recording: `{meta.get('file', '?')}` · rows: {len(rows)} of {len(golden)} golden · "
             f"prompts: {meta.get('prompts', '?')} · approved chunks: {meta.get('chunks', '?')} · "
             f"generator: {meta.get('text_model', '?')} → {meta.get('fallback_model', '?')} · "
             f"judge: {meta.get('judge_model') or 'none'}"), ""]
    if mode == "fake":
        head += [("Fake mode: the model never answers and embeddings are hashed, so answer-quality,"
                  " latency and judge metrics are n/a and intent beyond the rules is not meaningful."
                  " Run `live` for the real numbers."), ""]
    head += [f"**Gate: {'PASS' if gate else 'FAIL'}**", "",
             "| Metric | Value | Target | Result |", "|---|---|---|---|"]
    fails = ["", "## Failed checks by row", "", "| Row | Group | Status | Failed | Question |",
             "|---|---|---|---|---|"]
    for r in rows:
        bad = [k for k, v in scored[r["id"]].items() if v is False]
        if bad:
            st = recs[r["id"]].get("body", {}).get("status") or recs[r["id"]].get("http")
            fails.append(f'| {r["id"]} | {r["group"]} | {st} | {", ".join(bad)} | '
                         f'{r["question"][:70].replace("|", "/")} |')
    return "\n".join(head + lines + fails) + "\n", gate


# ====================================================================== recording (app)

def prompt_versions() -> str:
    out = []
    for name in ("qa_answer", "intent"):
        text = (ROOT / "backend" / "prompts" / f"{name}.md").read_text(encoding="utf-8")
        m = re.search(r"version:\s*(\S+)", text)
        out.append(f"{name} v{m.group(1) if m else '?'}")
    return ", ".join(out)


def record(mode: str, golden: list[dict], pace: float) -> Path:
    import os
    import tempfile
    import uuid

    os.environ["PROVIDER_MODE"] = "fake" if mode == "fake" else "live"
    os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient

    from app import auth
    from app.config import settings
    from app.db import connect
    from app.llm import gateway
    from app.main import app
    from app.rag.verify import quote_ok

    settings.llm_cache_dir = Path(tempfile.mkdtemp(prefix="qa-eval-cache-"))  # no cache hits
    client = TestClient(app)
    usage: list[dict] = []
    real_call = gateway.chat

    def counting_chat(task, system, user, timeout):
        raw, u = real_call(task, system, user, timeout)
        usage.append({"task": task, "provider": u.get("provider"),
                      "input": u.get("input", 0) or 0, "output": u.get("output", 0) or 0})
        return raw, u

    gateway.chat = counting_chat

    def new_user() -> dict:
        uid = uuid.uuid4()
        auth.save_user(uid, f"qa-eval-{uid.hex[:6]}")
        return {"Authorization": f"Bearer {auth.make_token(uid)}"}

    def new_report(h: dict, fixture: str) -> str | None:
        lat, lng, text = FIXTURES[fixture]
        before = settings.provider_mode
        settings.provider_mode = "fake"  # intake never spends live tokens
        try:
            r = client.post("/api/v1/reports", headers=h, data={
                "text": text, "lat": lat, "lng": lng, "pin_source": "gps", "accuracy_m": 10,
                "location_verified": "true", "ui_lang": "en"})
        finally:
            settings.provider_mode = before
        b = r.json()
        return b["report_id"] if b.get("state") in ("dossier_ready", "out_of_coverage") else None

    def quote_checks(body: dict) -> dict:
        out = {}
        with connect() as conn:
            for s in body.get("sources") or []:
                if s.get("kind") != "document":
                    continue
                texts = [t for (t,) in conn.execute(
                    "SELECT c.text FROM chunk c JOIN policy_doc d ON d.id = c.doc_id"
                    " WHERE d.url = %s AND c.status = 'approved'", (s.get("url"),))]
                out[s["id"]] = any(quote_ok(s["quote"], t) for t in texts)
        return out

    def dropped_of(endpoint: str, message_id) -> list:
        if not message_id:
            return []
        table = "chat_event" if endpoint == "chat" else "report_event"
        with connect() as conn:
            row = conn.execute(f"SELECT payload->'dropped' FROM {table} WHERE id = %s",
                               (message_id,)).fetchone()
        return (row[0] or []) if row else []

    with connect() as conn:
        chunks = conn.execute("SELECT count(*) FROM chunk WHERE status = 'approved'").fetchone()[0]
    started = datetime.now()
    RECORDINGS.mkdir(exist_ok=True)
    path = RECORDINGS / f"{mode}-{started:%Y%m%d-%H%M}.jsonl"
    meta = {"meta": True, "mode": mode, "started": started.isoformat(timespec="minutes"),
            "prompts": prompt_versions(), "chunks": chunks, "file": path.name,
            "text_model": settings.text_model, "fallback_model": settings.fallback_model,
            "intent_model": settings.intent_model, "embed_model": settings.embed_model}
    sessions: dict[str, tuple[dict, str | None]] = {}  # row id -> (headers, report id)
    recs: dict[str, dict] = {}
    model_rows, t_first, t_last = 0, None, None
    for row in golden:
        if row.get("followup_of") in sessions:
            h, rid = sessions[row["followup_of"]]
        else:
            h = new_user()
            rid = new_report(h, row["report_fixture"]) if row["endpoint"] == "ask" else None
        sessions[row["id"]] = (h, rid)
        if row["endpoint"] == "ask" and not rid:
            recs[row["id"]] = {"id": row["id"], "http": None, "error": "fixture report failed"}
            continue
        usage.clear()
        msg_id = str(uuid.uuid4())
        if row["endpoint"] == "ask":
            url, body = f"/api/v1/reports/{rid}/ask", {"question": row["question"],
                                                         "client_msg_id": msg_id}
        else:
            url, body = "/api/v1/chat", {"message": row["question"], "client_msg_id": msg_id}
            if row.get("send_state"):
                body["state_code"] = row["send_state"]
        t = time.monotonic()
        r = client.post(url, headers=h, json=body)
        dt = time.monotonic() - t
        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        rec = {"id": row["id"], "http": r.status_code, "latency_s": round(dt, 2),
               "body": resp if r.status_code == 200 else {}, "error": None if r.status_code == 200
               else resp, "usage": list(usage), "at": round(time.time(), 1)}
        if r.status_code == 200:
            rec["quote_in_chunk"] = quote_checks(resp)
            rec["dropped"] = dropped_of(row["endpoint"], resp.get("message_id"))
        recs[row["id"]] = rec
        status = rec["body"].get("status")
        print(f'{row["id"]:10} {r.status_code} {status or "":14} {rec["body"].get("intent") or "":14}'
              f' {dt:5.1f}s {row["question"][:60]}', flush=True)
        if any(u["task"] == "qa_answer" for u in usage) or r.status_code == 503:
            model_rows += 1
            t_first = t_first or t
            t_last = time.monotonic()
            if mode == "live":
                time.sleep(60 if r.status_code == 503 else max(0.0, pace - dt))
        elif mode == "live":
            time.sleep(1)
    if mode == "live" and t_first:
        meta["survival"] = {"rows": model_rows, "minutes": round((t_last - t_first) / 60, 2)}
    write_recording(path, meta, recs)
    return path


def write_recording(path: Path, meta: dict, recs: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for rec in recs.values():
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def secret(name: str) -> str:
    """A key from the environment, else from the root .env (never printed)."""
    import os

    if os.getenv(name):
        return os.environ[name]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    sys.exit(f"{name} is not set")


def judge(golden: list[dict], recs: dict, limit: int, embed_model: str) -> None:
    """Ragas faithfulness + answer relevancy and two DeepEval GEval rubrics, on at most
    `limit` answered rows, spaced out for the Groq free tier. Contexts = the shown quotes."""
    import asyncio

    from deepeval.metrics import GEval
    from deepeval.models import LiteLLMModel
    from deepeval.test_case import LLMTestCase, SingleTurnParams
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics.collections import AnswerRelevancy, Faithfulness

    groq_key = secret("GROQ_API_KEY")
    groq = AsyncOpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
    gemini = AsyncOpenAI(api_key=secret("GOOGLE_API_KEY"),
                         base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    llm = llm_factory(JUDGE_MODEL, client=groq)
    faith = Faithfulness(llm=llm)
    relevancy = AnswerRelevancy(llm=llm, embeddings=OpenAIEmbeddings(
        client=gemini, model=embed_model), strictness=1)
    jm = LiteLLMModel(model=f"groq/{JUDGE_MODEL}", api_key=groq_key, temperature=0)
    params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT]
    need = GEval(name="addresses the user's need", model=jm, evaluation_params=params, async_mode=False,
                 evaluation_steps=[
                     ("Does the answer say who is responsible, where to complain, and how, as far"
                      " as the question asks?"),
                     "Penalise filler that does not help (counts, statistics, unrelated facts).",
                     "Penalise answers that do not address the question at all."])
    authority = GEval(name="right authority + channel", model=jm, evaluation_params=params,
                      async_mode=False, evaluation_steps=[
                          ("Work out from the question whether the issue is local (municipal /"
                           " panchayat), state, or central-government."),
                          ("Penalise heavily recommending CPGRAMS or a central body for a local or"
                           " state issue, or a Delhi body for a place outside Delhi."),
                          "Reward naming the right level of authority and the state / local channel."])
    by_id = {r["id"]: r for r in golden}
    done = 0
    for rid, rec in recs.items():
        body = rec.get("body") or {}
        if body.get("status") != "answered" or rec.get("judge") or done >= limit:
            continue
        q, a = by_id[rid]["question"], answer_text(body)
        a_full = a + " Where to ask: " + ", ".join(w["label"] for w in body.get("where_to_ask") or [])
        ctx = [s["quote"] for s in body.get("sources") or []]
        case = LLMTestCase(input=q, actual_output=a_full)
        calls = {
            "ragas_faithfulness": lambda q=q, a=a, ctx=ctx: asyncio.run(faith.ascore(
                user_input=q, response=a, retrieved_contexts=ctx)).value,
            "ragas_answer_relevancy": lambda q=q, a=a: asyncio.run(relevancy.ascore(
                user_input=q, response=a)).value,
            "geval_need": lambda case=case: need.measure(case),
            "geval_authority": lambda case=case: authority.measure(case)}
        j = {}
        for key, fn in calls.items():
            try:
                j[key] = float(fn())
            except Exception as e:  # noqa: BLE001 - a judge failure is recorded, not fatal
                j[key], j[key + "_error"] = None, type(e).__name__
            time.sleep(6)  # the judge shares the Groq free tier
        rec["judge"] = j
        done += 1
        print(f"judged {rid}: {j}", flush=True)


# ====================================================================== main

def read_recording(path: Path) -> tuple[dict, dict]:
    meta, recs = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("meta"):
            meta = obj
        else:
            recs[obj["id"]] = obj
    meta.setdefault("file", path.name)
    return meta, recs


def newest() -> Path:
    for prefix in ("live-", "fake-"):
        found = sorted(RECORDINGS.glob(prefix + "*.jsonl"))
        if found:
            return found[-1]
    sys.exit("No recording in eval/recordings/: run `fake` or `live` first.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", choices=["replay", "fake", "live", "judge"])
    ap.add_argument("--recording", type=Path)
    ap.add_argument("--only", help="comma list of row ids")
    ap.add_argument("--pace", type=float, default=20.0, help="live: seconds per model-bound row")
    ap.add_argument("--limit", type=int, default=20, help="judge: at most N answered rows")
    ap.add_argument("--check", action="store_true", help="exit 1 when the gate fails")
    args = ap.parse_args()
    golden = load_golden()
    if args.only:
        keep = set(args.only.split(","))
        golden = [r for r in golden if r["id"] in keep or any(
            r["id"] == g.get("followup_of") for g in golden if g["id"] in keep)]
    if args.mode in ("fake", "live"):
        path = record(args.mode, golden, args.pace)
    else:
        path = args.recording or newest()
    meta, recs = read_recording(path)
    if args.mode == "judge":
        if meta.get("mode") != "live":
            sys.exit("judge needs a live recording")
        judge(golden, recs, args.limit, meta.get("embed_model", "gemini-embedding-001"))
        meta["judge_model"] = JUDGE_MODEL
        write_recording(path, meta, recs)
    report, gate = summarize(load_golden(), recs, meta)
    REPORT.write_text(report, encoding="utf-8", newline="\n")
    print(f"\n{REPORT.relative_to(ROOT)} written · gate {'PASS' if gate else 'FAIL'}")
    if args.check and not gate:
        sys.exit(1)


if __name__ == "__main__":
    main()
