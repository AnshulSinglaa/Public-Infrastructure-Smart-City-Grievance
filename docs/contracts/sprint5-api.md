# Sprint 5 API contract: real department routing (73 rules) + chat intake

Extends sprints 2–4; everything there still holds. Night-safety priority was **removed** by the user (2026-10-30).

## 1. Routing: plain SQL + rules, never the AI
Data: `data/rules_draft.csv` (73 rules), `data/departments.csv` (32 depts + contacts), `data/special_zones.geojson` (owner zones + lookup layers), `data/osm_roads.geojson`, `data/pwd_list.csv` / `pwd_matches.csv`. These are loaded into the DB by `scripts/load_rules.py` (rules, departments, contacts) and the existing loaders.

```
 pin ─► ward (25 covered?) ──no──► out_of_coverage (as before)
   │yes
   ▼
 owner zone? (ndmc_area, cantonment, yamuna_floodplain, dda_land, metro_premises, railway_land,
              industrial_area, asi_protected, forest_ridge, cpwd_colony, jj_cluster) by priority
   ▼
 nearest road ≤ 25 m → NH-48 ref → NHAI   (RD-01, priority 40: the 2026 MoRTH handover beats the list)
                     → on PWD list (confirmed match) → PWD
                     → else width: row_m ≥ 18.3 (60 ft) → PWD, else MCD;
                       width unknown → image road_judgement (wide/narrow, conf ≥ 0.7) → else ASK road_class
   ▼
 category + zone + road class + asset → first matching rule (lowest priority number) → department
   ▼
 lookups: discom_area (streetlight / power / live wire / transformer; nearest area within 15 m,
          like the ward buffer) → BRPL / BYPL / TPDDL
          unknown discom → ASK which_discom ("Which company sends your electricity bill?")
          ifc_trunk_drain within 25 m (drain categories) → I&FC
          drain category, drain type unknown → ASK drain_type
```
- **Confidence** = rule confidence × the **weakest** of its evidence (PWD list 1.0 · NH-48 ref 1.0 · PWD list via the other carriageway 0.95 · user answer 0.95 · width 0.9 · image judgement 0.85) × 0.8 if no citation × 0.9 if stale (`last_verified`, a `rules_draft.csv` column, older than a year; empty = unknown, no factor). A rule's **2nd candidate** card counts × 0.8 more.
- Lanes from the data: a `residential` / `service` / `living_street` way counts as narrow (evidence `width`) without asking.
- **Divided roads** (`pwd_list_parallel`): OSM often draws a divided road as two ways and only one is on the PWD list. If the nearest way is a `trunk` / `primary` / `secondary` road that is NOT listed, but a PWD-listed way of the same `highway_class` runs **alongside** it: at least 30 m of the unlisted way lies within 25 m of the listed one (way to way, not pin to way: the listed twin can be much shorter, B-041; a way that only touches the listed one end to end is not a twin, B-051), with a bearing within 20° (each way's bearing is taken on its ~20 m piece nearest the pin), the listed way is used. Evidence `pwd_list_parallel` (×0.95), why: "Divided road; the other carriageway is on the PWD list". Never for `residential` / `service` / `unclassified` roads, so a colony lane beside a main road stays with MCD. Example: Mahatma Gandhi Marg near Lajpat Nagar (28.5714N 77.2568E), ways 37751255 (unlisted) and 312031901 (listed), both give PWD.
- Below 0.7 with two different departments → `pick_candidate` (two cards; `not_sure` on `which_discom` gives three). Same department either way → no question.
- **Mathura Road (NH-2) stretches**: always `pick_candidate` PWD vs NHAI (takeover approved in principle).
- The dossier's `routing` gains `{"rule_id", "evidence": ["pwd_list" | "pwd_list_parallel" | "road_ref" | "width" | "image" | "user_answer" | "zone:<type>"], "candidates": [...], "why_key"}` and `"why"`: a one-line plain-English reason ("Road is on the PWD list (Mathura Road), so PWD maintains it").

## 2. Questions (LangGraph interrupts), one generic answer endpoint
New `next.kind` values (envelope unchanged): `issue_question`, `pick_candidate` (plus the existing `clarify_category`).
```json
{"kind": "issue_question", "question_id": "road_class | drain_type | ghat_position | which_discom",
 "text": "Is this on a main road or an inner lane?",
 "options": [{"value": "main_road", "label": "Main road"}, {"value": "inner_lane", "label": "Inner lane / gali"},
             {"value": "not_sure", "label": "Not sure"}]}
{"kind": "pick_candidate", "text": "More than one department could handle this. Which fits?",
 "candidates": [{"department": {"code": "PWD", "name_en": "…"}, "confidence_label": "likely", "why": "…"}, …]}
```
**`POST /api/v1/reports/{id}/answer`**, body `{"value": "inner_lane"}` (one of the options) **or** `{"text": "bses wala, laal logo"}` (free text in any language).
- `text` is at most **300 characters** (longer → 422 `invalid_answer`, B-048); `value` must be one of the options.
- Free text costs a model call, so it is limited (SEC-015): **5 per report and 30 per user in a rolling 24 h** → 429 `rate_limited_answers` (`code` `rate_limited`), checked in SQL under the per-user lock before the model is called. Button `value` answers are not limited. Each model call leaves a `model_call` row in `report_event` (not shown in `/conversation`).
- Free text is mapped to an option by the text LLM (strict: must return one of the option values or `unclear`). `unclear` → the same question again with `"retry": true`. Vague text and instructions ("ignore all rules…", "you must answer X") are `unclear`; `not_sure` only when the user clearly says they don't know. The LLM never sees routing data and never returns a department: option values are neutral ids (`which_discom`: `brpl` / `bypl` / `tpddl`, mapped to BRPL / BYPL / TPDDL in code; `pick_candidate`: the button values are department codes, but the LLM sees only `option_1..n`, with the department names as labels, and its answer is mapped back in code).
- `not_sure` on `issue_question` → `pick_candidate` (cards sorted most likely first). Answering `pick_candidate` sets the department: evidence = the chosen card's own evidence + `user_answer` once (a 2nd candidate has none of its own), `why_key` `why_user_answer`, why "You chose <dept>. <that card's reason>".
- Atomic like `/clarify` (TL#2): wrong or no pending question → 409 `wrong_step`; invalid value → 422 with `message_key` `invalid_answer` (`code` `validation`). The report row is locked and the pending question re-checked **after** the LLM call, before anything is written: an answer that lost a race gets 409 and writes no event (B-039).
- `/clarify` stays as is (it's the `clarify_category` answer). `/answer` also accepts `clarify_category` answers, so the UI can use one endpoint.

## 3. Chat transcript (for the chat UI)
`GET /api/v1/reports/{id}/conversation` → `{"messages": [{"role": "user" | "assistant", "kind": "report | question | answer | emergency | dossier | rejected | out_of_coverage", "text": "…", "options": [...]?, "dossier_id": "…"?, "at": "…"}]}`
Built from stored report events (`report_event` table: report_id, role, kind, payload jsonb, at). Every interrupt and answer is recorded. Owner-only (404 otherwise). Text is masked.
Buttons survive a refresh: `question` messages carry `options` ([{value, label}], plus `why` and `confidence_label` per pick_candidate card) and `retry`; `emergency` carries the call-first numbers and `out_of_coverage` the same channels as the POST /reports response (Delhi helplines, or the state portal if verified + CPGRAMS), both as `options: [{value, label, deep_link}]`; `out_of_coverage` also has `outside_delhi`; `rejected` has the retake `reason`.

## 4. New error / message keys
`invalid_answer` (422) · `rate_limited_answers` (429) · `answer_unclear` (re-ask) · `q_road_class`, `q_drain_type`, `q_ghat_position`, `q_which_discom`, `q_pick_candidate` (question texts the UI can override) · `why_pwd_list`, `why_pwd_list_parallel`, `why_road_ref`, `why_width`, `why_zone`, `why_rule`, `why_user_answer` (reason templates)


## 5. Tables (migration 0005; `scripts/load_rules.py` fills them)
All upserts are by the key shown. The backend reads these tables only.

**`routing_rule`** (rebuilt; one row per `rules_draft.csv` row, key `rule_id`)
| column | type | from the CSV |
|---|---|---|
| `rule_id` | text PK | `rule_id` (`RD-03`) |
| `priority` | int | `priority` (lower = checked first) |
| `categories` | text[] | `category` split on `;` (`{'*'}` = any category) |
| `condition` | text | `condition` (prose; how it is checked is in `backend/app/routing/conditions.py`, keyed by `rule_id`) |
| `department_code` | text | `department_code` (a `departments.csv` code) |
| `sla_days` | int, null | `sla_days` |
| `sla_source_url`, `rule_source_url` | text, null | same names (empty → NULL; no `rule_source_url` = uncited, ×0.8) |
| `confidence` | real | `confidence` |
| `notes` | text, null | `notes` |
| `dataset_version` | int, null | FK `dataset_version.id` |
| `last_verified` | date, null | `last_verified` column (empty = unknown); older than a year = stale (×0.9) |

`master_issue.routing_rule_id`, `routing_decision.rule_id` and `outcome_feedback.routing_rule_id` are now `text` FKs to `routing_rule.rule_id`.

**`department`** (key `code`): existing `code, name_en, name_hi` + new `level, owns, jurisdiction_type, helpline, grievance_url, email, source_url, accessed (date), verified` (all text except `accessed`), same names as `departments.csv`.

**`contact`** (unique `department_id, channel_type, value`): one row per channel, e.g. `('helpline', '1916', 'tel:1916')`, `('portal', <grievance_url>, <grievance_url>)`, `('email', …)`. The dossier's `channels` are the department's contact rows (if a department has none, its `departments.csv` helpline + portal are used).

**`road_segment`** (from `osm_roads.geojson` + `pwd_matches.csv`): existing `osm_way_id, name, highway_class, geom (LineString 4326), pwd_listed` + new `ref` (OSM ref, e.g. `NH48`; several joined by `;`), `row_m` (right-of-way in metres, null = unknown), `pwd_road_id` (`RD-01219`, confirmed matches only), `ward_no`. GIST index on `geom::geography` for the 25 m search. Negative `osm_way_id`s are test rows.

**`report_event`**: `id bigserial, report_id uuid → report (cascade), role ('user' | 'assistant'), kind, payload jsonb (masked text only), at timestamptz`. Written by the backend only.
