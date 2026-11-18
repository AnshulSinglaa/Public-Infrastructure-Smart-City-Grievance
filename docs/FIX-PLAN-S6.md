# Sprint 6 chat: senior review and fix plan (2026-11-15)

Written after the user's live test. **The chat is grounded but not yet helpful.** Every sentence
had a source, yet the answers missed the user's real need, and one gave wrong advice. Our checks
measure "is it backed by a document", not "does it solve the problem correctly". This plan fixes both.

## The failing case (keep it as a regression test)

> Q1: "I belong to a very small town called Badhra in haryana. from very few days there is water
> clogging in front of my house idk what to do where to raise this concern"
> Q2: "can u tell me step by step how to report this issue"

| # | What happened | Severity |
|---|---|---|
| P1 | English question → **Hindi (Devanagari) answer** | major |
| P2 | Answer padded with **irrelevant CPGRAMS totals** (1,624,414 received…) | major |
| P3 | Never said **who is responsible** (the local Municipal Committee for a town drain) | major |
| P4 | Q2 recommended **CPGRAMS**, which is for central ministries, for a local Haryana drain: grounded but **misapplied** | **critical** |
| P5 | "Water in front of **my** house" was not offered **"Start a report?"** | minor |
| P6 | Answers fail on the free tier: **Groq 429**, then **Gemini timeout/503** → "AI unavailable" | **critical for the demo** |
| P7 | "How do I file an RTI?" → out of scope (**RTI Act text missing**); layer label "delhi" for a national question | major |
| P8 | Source **titles are empty** (`title: None`), so the chips have no names | minor |
| P9 | Wrong `layer` label on fixed replies (e.g. "hi" → state) | minor |
| P10 | Logs: LiteLLM "Give Feedback / Get Help" noise; the question text is printed (must be masked only) | minor |

## Root causes (why it happened)

1. **No task structure in the answer prompt.** The model is told "answer from these passages", so it
   summarises whatever was retrieved (stats included) instead of answering *who → where → how*.
2. **Documents carry no "applicability" metadata.** Retrieval can't know CPGRAMS is only for central
   matters, so a thin Haryana corpus lets a national document win.
3. **The follow-up loses context.** The last 4 raw turns are passed, but no **extracted facts**
   (state, town, issue type, "it's my own problem"), so Q2 forgot "Haryana, local drain".
4. **No language rule.** The model picked Hindi because the chunks were Hindi or Haryana-flavoured.
5. **One intent label only.** "My problem + a question" needs both an answer and a report offer.
6. **Prompts are too big for the free tier**, with no throttle or retry-after handling, and the Gemini
   fallback timeout/model is wrong or too tight.
7. **Evaluation measures faithfulness only**, so helpfulness and applicability were never tested.

## Required changes

### A. Answer planner (backend, `app/rag/answer.py` + prompt)
- The model returns **structured JSON**, not free prose:
  `responsible_body`, `where_to_complain` (channel + link), `steps[]`, `escalation`, `notes`,
  each with its citation ids. The server renders it as short sentences in that order.
- **Rules in the prompt and checked in code:** answer the user's need first; **no statistics or
  numbers unless the user asked for them**; at most ~5 sentences; never recommend a channel whose
  document says it doesn't cover this level (see B).
- If the corpus has no responsible body for the place, say it honestly with the generic local
  answer from C: "Usually the local municipal body (here: Municipal Committee Badhra) handles
  drains in a town", labelled as a general rule, never as a cited fact.

### B. Applicability metadata (data + retrieval)
- Add to each source in `data/corpus/sources.yaml`:
  - `level: central | state | local`
  - `covers: [...]` (issue types or "all")
  - `not_for: [...]`
  For example, CPGRAMS: `level: central`, `not_for: [state_matters, local_body_matters]`; a state CM portal: `level: state, covers: [all]`.
- Copy them onto `policy_doc` and the Qdrant payload. **Retrieval filters by the issue's level:**
  local and state issues → state + local documents first; national documents only for
  national/central matters, or for RTI as a general right.
- **Code check:** a sentence recommending a channel whose `not_for` matches the issue is dropped.

### C. Local body lookup for anywhere in India (data, the big win)
- Load India's official **Local Government Directory (LGD)** list of urban local bodies
  (lgdirectory.gov.in, public download): body name, type (Municipal Corporation / Council /
  Committee / Nagar Panchayat), district, state, plus the gram panchayats if it's feasible.
- A tool step inside the LangGraph/QA flow: **town or GPS → the actual local body name + type**.
  Badhra → "Municipal Committee Badhra, Charkhi Dadri, Haryana". This answers "who is responsible"
  for local issues anywhere, honestly labelled as "your local body".
- Combine it with `state_directory.csv` for the state portal and helpline.

### D. Conversation memory (backend)
- Keep **extracted slots** per chat thread: `state`, `town/city`, `issue_type`, `own_problem`,
  `local_body`. Fill them from each message (rules + a small model) and pass them, not only raw
  turns, to retrieval and the prompt. Q2 must still know "Haryana, Badhra, waterlogging, local".

### E. Language rule (backend)
- Detect the user's script/language in code: English → reply in English; Hinglish (Latin letters)
  → Hinglish in Latin letters; Hindi (Devanagari) → Hindi. Put it in the prompt and **check it in
  the output** (Devanagari ratio); one retry, else fall back to English.

### F. Intent (backend)
- Allow **multi-intent**: `general_civic` + an `own_problem` flag → answer **and** add the
  `start_report` action, prefilled with the masked text plus the town.

### G. Free-tier reliability (backend, critical for the live demo)
- Cut the prompt: about 4 chunks, each trimmed around the matched sentences; a target of ≤ 1.5k input tokens.
  Measure the before/after tokens per answer.
- Choose the Groq model for `qa_answer` by **free-tier limits** (check the console: TPM/RPM/RPD).
  Keep gpt-oss-120b only if the limits allow.
- Gateway: on a Groq 429 with `retry-after` ≤ 3 s, wait and retry once; otherwise go to Gemini.
  The Gemini fallback must use a correct, available text model with a realistic timeout (≥ 20 s).
  Log the real Gemini error.
- A **per-minute throttle/queue** for live QA calls, and a friendly "busy, try again in N s" with
  the countdown. Cache identical questions (already keyed on the full prompt).

### H. Small fixes
- P7: support `data/corpus/manual/` files (git-ignored PDFs the user downloads in a browser) with
  their official URL as the source; the national layer label is correct for RTI/NH questions.
- P8: titles from sources.yaml `name`, or the HTML `<title>` / PDF metadata; backfill the existing rows.
- P9: the layer label on fixed replies = the detected layer, or "none".
- P10: `litellm.suppress_debug_info = True`, quiet LiteLLM logging; log **masked** text only.

## Evaluation upgrades (QA + backend)
Add to the golden set, and gate on them:
- **Helpfulness / answer relevance** (Ragas answer_relevancy, DeepEval GEval "addresses the user's need").
- **Right authority:** the expected `responsible_body` level and name for about 20 places (Delhi wards,
  Badhra, Jaipur, Mumbai, a village).
- **Right channel:** never CPGRAMS for state or local issues; state portal for state issues.
- **Language match:** the reply script equals the question script.
- **No irrelevant numbers:** a number appears only if the question asked for one.
- **Follow-up memory:** Q2-style follow-ups keep the place and the issue.
- Latency p50/p95, and **free-tier survival**: 30 questions in 10 minutes without a 503.

## Done when
- The Badhra Q1 and Q2 give: English; Municipal Committee Badhra as the likely owner (general rule,
  labelled); the Haryana CM Window as the channel; short steps; a "Start a report?" button; no CPGRAMS
  for this local issue; no irrelevant numbers.
- All the new eval checks pass their targets, and the full test suite is green.
