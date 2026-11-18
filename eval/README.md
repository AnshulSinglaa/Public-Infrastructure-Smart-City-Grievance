# Q&A eval

`qa_golden.jsonl` has 92 rows: the PLAN §10.3 set (72) plus 20 rows for the FIX-PLAN-S6
"Evaluation upgrades" (right authority, right channel, language, no irrelevant numbers,
follow-up memory). Each row lists the expected intent, status (`expect`), layer,
`local_rule_unknown` (`lru`), the authority (`level`, `names_any`), allowed and forbidden
channels, the language, `must_not_contain`, `numbers_allowed`, and `followup_of`.

Two environments: `fake` / `live` import the app, so they run in the **root** project; the judges
run in the eval's **own** uv project (`eval/pyproject.toml` + `eval/uv.lock`: ragas, deepeval,
langchain), so they never change the app's dependencies (ragas pins openai 3.3 and
langchain-community < 0.4). `replay` is plain Python and runs in either.

| Mode | Environment | Needs | What it does |
|---|---|---|---|
| `fake` | root | local DB + Qdrant | Every row in-process with `PROVIDER_MODE=fake` → `recordings/fake-*.jsonl`. |
| `live` | root | local DB + Qdrant + keys | Real Groq → Gemini, one model-bound row every `--pace` s (default 20 s: 30 answer rows take 10 min = the free-tier survival check) → `recordings/live-*.jsonl`. |
| `judge` | eval | `GROQ_API_KEY`, `GOOGLE_API_KEY` (env or root `.env`) | Ragas + DeepEval on at most `--limit` answered rows of a live recording (judge `qwen/qwen3.8-27b` on Groq, 6 s apart), written back into it. |
| `replay` | either (CI: eval) | nothing | Scores the newest recording (`live-*` first, else `fake-*`) → `report.md`. |

```
uv run python eval/run_eval.py fake
uv run python eval/run_eval.py live --pace 20                        # ~30 min, after the fixes
uv run --project eval python eval/run_eval.py judge --limit 20       # ~10 min
uv run --project eval python eval/run_eval.py replay --check         # exit 1 if the gate fails
uv run --project eval pytest eval -q -p no:cacheprovider --rootdir eval -o pythonpath=eval -o testpaths=eval
```

Every row runs as a new user, so a row never inherits another row's state. A follow-up
(`followup_of`) reuses the earlier row's user. Each `/ask` row gets a fresh report made with
the fake intake (pins in `FIXTURES`), so intake spends no live tokens. Live runs use an empty
LLM cache, which means no cached answers. Eval users stay in the local DB (`qa-eval-*`).

**Metrics:**
- Code checks:
  - citation exactness (each shown quote is re-checked against its stored chunk);
  - the numbers in each sentence appear in the shown quotes;
  - no irrelevant numbers;
  - layer and `local_rule_unknown`;
  - refuse / off-topic / blocked / fixed rows;
  - 0 answer-model tokens on fixed replies;
  - false refusals;
  - intent accuracy (overall, off_domain, greeting, civic→off_domain);
  - right channel (no CPGRAMS for state or local issues), must-not-contain, actions offered, right authority, channel offered, language and script, follow-up memory;
  - cite-or-drop pass rate;
  - latency p50 / p95, tokens per answer, free-tier survival.
- Judges: Ragas faithfulness and answer_relevancy, plus DeepEval GEval "addresses the user's need" and "right authority + channel".

**Not covered yet:**
- Ragas context precision.
- "Expected citation in the top 6": the rows have no `expected_citations` yet. They need a second labeller (PLAN §10.3).
- DeepEval HallucinationMetric: GEval and Ragas faithfulness cover it.

**CI:** the `eval-replay` job in `.github/workflows/ci.yml` runs `uv sync --project eval`, the scorer
self-test, then `replay --check` on the committed recording. It is `continue-on-error` until the first
passing live recording is committed; then remove that line so it gates merges.
Commit `recordings/live-*.jsonl` after each live run that changes the prompt, model, thresholds or corpus.
