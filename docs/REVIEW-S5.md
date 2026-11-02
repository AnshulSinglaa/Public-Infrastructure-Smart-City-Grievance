# Sprint 5 tech-lead review

Scope: working tree vs HEAD (7f65f63), against `docs/contracts/sprint5-api.md`. Read-only review; nothing was changed or run against the DB.

**Verdict: needs changes before merge.** There is 1 must-fix (a report can get stuck) and a few should-fixes (spatial indexes, migration downgrade, contract drift, unused code). The security core holds up: the LLM never picks a department, `/answer` text is masked before Groq, owner checks are on both new endpoints, `claim()` re-checks the pending question after the LLM call, and error bodies are generic.

Severity: **must-fix** blocks the merge · **should-fix** fix this sprint · **nit** optional.

## 1. Correctness

| # | Where | Sev | Finding | Fix |
|---|---|---|---|---|
| C1 | backend/app/api/reports.py:243-249 | must-fix | `resume()` commits `state = 'resuming'` and only then runs the graph. If `run_intake` raises (DB error, empty `routing_rule`, a bug), the report stays `resuming` for good, and every later `/answer` or `/clarify` returns 409 `wrong_step`. The user can't finish the report. | Wrap `run_intake` in try/except: on an exception, set the state back to `needs_clarification`, then re-raise. **Done (2 Nov, debugger):** `resume()` sets `needs_clarification` back on any exception, then re-raises; test `test_a_crash_while_resuming_does_not_leave_the_report_stuck`. |
| C2 | backend/app/routing/engine.py:184-199 (`discom`, `ifc_drain`), 30-35 (`find_ward`) | should-fix | `ST_DWithin(geom::geography, …)` on `special_zone` and `ward` has no `geography` index. Only the `geometry` GIST exists (0001:111,122). Every decide/resume (and every live-wire call-first) seq-scans and computes geodesic distance against big discom polygons. | In migration 0005 add `CREATE INDEX … ON special_zone USING gist ((geom::geography))` and the same on `ward`. Confirm with `EXPLAIN`. **Done (2 Nov, debugger):** migration 0006: `ward_geog_idx` + `special_zone_geog_idx`; EXPLAIN shows Index Scan for discom and find_ward. |
| C3 | backend/migrations/versions/0005_routing_rules.py:290-311 | should-fix | The downgrade isn't a true inverse. It recreates the old `routing_rule` but leaves out the 3 FKs (`master_issue`, `routing_decision`, `outcome_feedback` → `routing_rule(id)`), `routing_rule_lookup_idx` and `ENABLE ROW LEVEL SECURITY`. So after a down, RLS is off on that table. | Add the 3 `ADD FOREIGN KEY`, the lookup index and `ALTER TABLE routing_rule ENABLE ROW LEVEL SECURITY` to `DOWN`. **Done (2 Nov, debugger):** DOWN now re-adds the lookup index, RLS and the 3 FKs; up → down 0004 → up → down 0005 → up passed on a throwaway DB. |
| C4 | backend/app/routing/engine.py:231-236 + data/rules_draft.csv (RD-01 prio 40 < RD-03 prio 42) | should-fix | Order drift. The contract §1 says "on PWD list → PWD, then NH-48 ref → NHAI", but the code (and the rule priorities) check NHAI first. A PWD-listed way tagged NH48 goes to NHAI, not PWD. | Decide which one is right. Then either swap the order in `fact("owner")` and the priorities, or change the contract's diagram. **Done (2 Nov, debugger):** rules data wins: RD-01 (cited 2026 MoRTH NH-48 handover) is priority 40, before RD-03. Code unchanged; contract diagram now NHAI first. |
| C5 | scripts/load_rules.py:89 | should-fix | `last_verified` is set to the CSV file's **mtime**. A `git checkout` or a copy resets it, so the "stale ×0.9" factor (engine.py:283) never really fires and says nothing about verification. | Add a `last_verified` column to `rules_draft.csv` and load that. Otherwise remove the stale factor from both the code and the contract. **Done (2 Nov, debugger):** `rules_draft.csv` has a `last_verified` column (RD-02 2026-10-27, others empty = unknown, no factor); the loader reads it, no mtime. |
| C6 | backend/app/routing/engine.py:120-121, 150 | should-fix | `ST_Azimuth` returns NULL when both interpolated points are the same: a closed way (roundabout) under ~20 m long has f−d clamped to 0 and f+d clamped to 1, which is the same point. Then `abs(None - …)` raises a TypeError, so the request returns a 500. | Skip twins whose `bearing` is None: `if road["bearing"] is None: return road`, and `continue` for a twin with None. **Done (2 Nov, debugger):** None guard for the way and each twin (a sub-20 m loop can't pass the 30 m alongside check now, but the guard stays). |
| C7 | backend/app/routing/engine.py:278-285 | nit | The confidence math uses the **weakest** evidence (`min`). The contract says "rule confidence × evidence", which reads as one factor. That matches only when there is one evidence. The 2nd-candidate ×0.8 (engine.py:323) isn't in the contract either. | Add "weakest evidence" and "2nd candidate ×0.8" to contract §1. **Done (2 Nov, debugger):** contract §1: weakest evidence + 2nd candidate ×0.8. |
| C8 | backend/app/routing/engine.py:354 + 358-366 | nit | `not_sure` on `which_discom` gives **3** cards (BRPL/BYPL/TPDDL), but the text says "Two departments…" and the contract lists only `option_1`/`option_2`. | Change the text to "More than one department could handle this", and in the contract write `option_1..n`. **Done (2 Nov, debugger):** text "More than one department could handle this. Which fits?"; contract says option_1..n and 3 cards for which_discom. |
| C9 | backend/app/routing/engine.py:165 | nit | On Mathura Road the evidence is `pwd_list` even when the way isn't on the list, so the UI pill says "On the PWD road list". | Use evidence `road_ref` (or a new `mathura_road`) for Mathura Road. |
| ok | reports.py:229-249, 289-310 | n/a | `claim()` locks the report row (`FOR UPDATE`, scoped to the owner) and re-checks the state and the pending question **after** the LLM call. The `resuming` state then blocks the losing request. The `unclear` path claims before it writes. This meets TL#2 / B-039. | none |
| ok | engine.py:118-148 | n/a | The road query: `ROAD_COLS`'s subquery gets flattened, so `road_segment_geog_idx` (0005:275) is used for both the pin search and the twin search. There's no N+1: about 6 queries per decide. | Confirm once with `EXPLAIN ANALYZE` on the MG Marg pin. |

## 2. Security

| # | Where | Sev | Finding | Fix |
|---|---|---|---|---|
| S1 | backend/app/intake/store.py:117-132 | should-fix | `department_id()` **inserts** into `department` at runtime when a code is missing. That breaks the contract §5 rule "The backend reads these tables only". It also hides a loader that wasn't run (`load_rules.py:156` already asserts this). | Delete the fallback: `SELECT id …` and raise if the row is missing. **Done (2 Nov, debugger):** `department_id()` only SELECTs and raises if missing; the test seed helper inserts departments itself. |
| S2 | backend/app/api/reports.py:293-297 | nit | For `pick_candidate` the model sees the department **names** as option labels. It still returns only `option_N` (checked at groq.py:78), so it can't choose a department. But this is a little more than "never sees routing data". | Accept it and say so in contract §2 ("labels = department names; values neutral"). |
| S3 | backend/prompts/map_answer.md:15 | nit | "pick option 2" → `unclear` also turns away a real pick_candidate reply like "the first one / option 1", so the user gets asked again. It's safe, just slightly annoying. | Allow ordinal answers for pick_candidate only, or leave it and note it as a known limitation. |
| ok | reports.py:280-290, 320; events.py | n/a | Owner check (404) on `/answer` and `/conversation`. Text is capped at 300 chars and `mask_pii` runs before Groq. The answer and report events store masked text only, and the other payloads are static text, public helplines or our own codes. Error bodies are `{code, message_key}` only, and `ProviderDown` → a generic 503. | none |

## 3. Simplicity / unused code

| # | Where | Sev | Finding | Fix |
|---|---|---|---|---|
| U1 | ui/strings.py (`q_road_class`, `q_drain_type`, `q_ghat_position`, `q_which_discom`, `q_pick_candidate`, the 7 `why_*` keys, `back_to_chat`) | should-fix | Nothing in the UI reads these 13 keys. The transcript doesn't store `message_key` / `why_key`, so the UI can't use them (the user's checklist says "no unused code"). | Remove them, or store `message_key` in the question events and use `t()` on it in `chat.py`. **Done (2 Nov, debugger):** 12 keys removed: 4 `q_*`, 7 `why_*` and `call_helpline`. `q_pick_candidate` stays: the API sends it as a message_key (test_every_api_error_key_has_text). `back_to_chat` is used (report.py), kept. |
| U2 | ui/chat.py:31-54, 182, 195, 202-213 | should-fix | The fallbacks through `envelope()` / `coverage_links()` aren't needed now: the transcript already carries `options`, `reason` and `outside_delhi`. The `envelope()` docstring ("stored as text only") is stale. `chat.py:51` hardcodes CPGRAMS a second time. | Keep `envelope()` only for `state_directory.state`, and remove `coverage_links` and the `emergency`/`reason` fallbacks. **Done (2 Nov, debugger):** `envelope()` / `coverage_links()` and the emergency / reason / channels fallbacks removed; only the state name comes from the session. The transcript now also stores the state helpline button. |
| U3 | backend/app/routing/engine.py:438-441 | nit | Only a test calls `route()` (test_sprint4_fixes.py:134). | Call `decide()` in the test and remove `route()`. |
| U4 | backend/app/routing/engine.py:255 | nit | `(have == want) == (key == "not_zone")` is clever, and a student would struggle to explain it at a viva. | Write it out: `mismatch = have == want if key == "not_zone" else have != want`. |
| U5 | backend/app/api/reports.py:235; backend/app/intake/graph.py:126 | nit | There's a broken line join (`…question"         and pending…`), with 9 spaces in the middle of the line. | Break it across 2 lines inside parentheses. |
| U6 | backend/app/routing/engine.py:316-318 | nit | `option()` only wraps a dict literal. | Use the dict literal directly, or keep it; it's harmless. |
| U7 | ui/account.py:90 | nit | `(json.loads(saved) + [None])[:4]  # older pages kept 3` handles old pages, but the JS now always returns 4 values (it's built from `STORE`). | Use `token, draft, photo, thread = json.loads(saved)`. |
| U8 | backend/app/api/reports.py:1-2 | nit | The module docstring stops at the sprint 2-4 contracts and leaves out `/answer` and `/conversation`. | Add `sprint5-api.md` and the 2 endpoints. |

## 4. Contract vs code drift

| # | Where | Sev | Finding | Fix |
|---|---|---|---|---|
| D1 | contract §1 vs engine.py:231 | should-fix | NHAI before the PWD list (see C4). | Make them match. **Done (2 Nov, debugger):** see C4. |
| D2 | contract §2 ("pick_candidate: option_1/option_2" as option values) vs events.py:31 | should-fix | API option `value`s for pick_candidate are **department codes**; `option_N` exists only inside the LLM call. | Contract: "button values are department codes; the LLM sees option_1..n". **Done (2 Nov, debugger):** contract: button values are department codes; the LLM sees option_1..n. |
| D3 | contract §5 "backend reads these tables only" vs store.py:123 | should-fix | See S1. | Remove the runtime insert. **Done (2 Nov, debugger):** see S1. |
| D4 | contract §1 vs engine.py:169 | nit | Not in the contract: `residential`/`service`/`living_street` count as narrow from the data (evidence `width`), without asking. | Add one line to contract §1. **Done (2 Nov, debugger):** one line added to contract §1. |
| D5 | contract §2 "422 `invalid_answer`" vs reports.py:282,284,313 | nit | The code returns `code: "validation"`, `message_key: "invalid_answer"`. | Contract: say that `invalid_answer` is the message_key. **Done (2 Nov, debugger):** contract says message_key `invalid_answer` (code `validation`). |
| D6 | contract §1 confidence vs engine.py:280,323 | nit | See C7. | Document it. **Done (2 Nov, debugger):** see C7. |

## Test coverage
`tests/test_routing.py` (30 tests) covers the parallel/divided-road cases, a race (B-039 at line 461) and transcript reasons. Missing tests:
- a resume where the graph raises (C1);
- a closed or very short way near the pin (C6);
- `not_sure` on `which_discom` (3 cards);
- a migration down → up round trip.

Not done this round (nits not in the fix list): C9, S2, S3, U3–U8.
