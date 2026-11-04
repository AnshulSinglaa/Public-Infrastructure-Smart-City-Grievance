# Roadmap: future work

Agreed with the user on 2026-11-03. The current build (Sprints 1–5, then the deploy mini-sprint and
Sprint 6) carries on as planned. Everything below is **future work**, to be started only after
those are done. Order and scope get confirmed with the user at the time.

## Why this exists (what the demo showed)

- **Coverage:** routing works only inside 25 curated Delhi wards. Real users can be anywhere in India,
  so most real reports get "We don't cover this area yet".
- **Nobody reports:** in real life one person might report a problem, and almost nobody else
  joins in. Features that need many reporters (duplicate counts, votes, hotspots) will rarely fire.
- **Who reports is biased:** richer areas complain more, so complaint data hides problems in poorer areas.

## V2-A: work anywhere in India (evidence ladder)

The 25 wards stop being a coverage limit and become the **hand-checked test set** used to measure accuracy.

```
 pin anywhere in India
   1. MAP FACTS       OpenStreetMap: road type, NH/SH tag, road name, city limits,
                      cantonment / railway land          (free, instant)
   2. MEMORY          this city + problem researched before? reuse it
   3. RESEARCH AGENT  web search on gov sites only (*.gov.in, *.nic.in);
                      keep a claim only if its quote is really on the page (cite-or-drop);
                      save the answer so the next user gets it instantly
   4. PHOTO CLUES     lanes, divider, footpath                    (adds confidence)
   5. ASK THE USER    1-2 easy questions ("Divider? Buses here?"), never "how many feet"
   6. STILL UNSURE    two candidates, or local body + state grievance portal (never guess)
```

- Legal backing (RTI Act, state time-bound-services acts) comes **only** from a curated legal
  knowledge base, never from the open web.
- Escalation for one citizen: deadline → appeal draft → RTI draft → first appeal draft (the user sends each).
- Closure check: the citizen's new photo at the same spot shows whether it's really fixed.

## V2-B: Road auditor (detect problems instead of waiting for complaints)

One phone or dashcam on one vehicle records the road. The AI finds the problems, so no citizen
has to report them.

```
 1. SEE       video + GPS → blur faces/plates → fine-tuned YOLO finds potholes, cracks,
              garbage piles, waterlogging, open manholes → VLM double-checks unsure ones
 2. REMEMBER  defect registry (PostGIS): the same pothole seen many times = one defect
 3. THINK     agents: who is responsible (V2-A ladder), how dangerous (size × road traffic ×
              near school/hospital), and one ready complaint per department
 4. CHECK     next drive re-sees the spot → really fixed, or only "closed" on paper?
              public map + department scorecards + equity view (defects vs complaints per area)
```

- **Two ways in, one brain:** citizen reports (built today) and vehicle video feed the same
  routing → complaint → check pipeline. Merged into this repo as Version 2.
- **Screens:** a map home page with a danger list · upload a drive (live AI steps) ·
  problem detail (who/why, ready complaint, before/after photos) · chat (ask about your area,
  or report one problem).
- **New tech:** PyTorch + Ultralytics YOLO (RDD2022 + TACO + frames from our own city),
  CLIP embeddings, a background worker for video jobs, MLflow. Everything else stays
  (FastAPI, LangGraph, Gemini, Groq, PostGIS, Qdrant, Langfuse, Ragas/DeepEval, Locust).
- **Frontend choice to make then:** keep Streamlit with a better map, or switch to Next.js + MapLibre (about 1 extra week).
- **Needs from the user:** 1–2 hours of road video from their city; help labelling about 1,000 frames.

**Evaluation (the numbers for the write-up):**
- detection mAP on our own city's test set
- the cascade's accuracy vs cost (YOLO only, VLM only, both)
- dedup precision
- location error in metres
- repair-check accuracy
- jurisdiction accuracy (LLM-only vs our agent)
- text-to-SQL accuracy for the chat

**Interview risks to close:**
- Show the full loop, not just detection; pothole detection alone is a common project.
- Have a believable user ready: city vehicles, delivery fleets or RWAs.
- File a few real complaints and show what happened.

## Other ideas (lower priority)

- **Tender cross-check:** match public road-repair tenders to what the camera still sees ("₹X sanctioned, Y potholes remain").
- **Recurring hotspots:** monsoon flooding, festival waste. Warn the local body before the next event.
- NGO partner matching; recycler matching.
- More languages; official integrations only through formal APIs and partnerships.
