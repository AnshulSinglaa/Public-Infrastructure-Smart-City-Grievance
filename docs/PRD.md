# PRD: Civic Grievance Router (Multimodal Agentic RAG)

| | |
|---|---|
| **Type** | College / portfolio project (not a startup) |
| **Platform** | Own web/mobile app (NOT WhatsApp) |
| **City** | Delhi only |
| **Languages** | Hindi + English (incl. Hinglish) |
| **Categories** | Pothole · Garbage dump · Waterlogging/sewage overflow · Broken streetlight |
| **Status** | v1 confirmed by user 2026-10-24 (all Must, Should, Could accepted). **2026-10-25: voice input removed. Input = photo + text + GPS** (overrides any "voice note" below). **2026-10-27: ALL civic problem types and ALL Delhi departments, ~25 wards** (overrides "4 categories" / "8 wards"; see TECH-MEETING-01) |
| **Tech stack** | Out of scope here (decided in ARCHITECTURE.md) |

---

## 1. Problem Statement

A citizen in Delhi sees a pothole, a garbage dump, a flooded road or a dead streetlight. To get it fixed they first have to answer a question they usually can't: **which agency owns this exact spot?**

- **Jurisdiction is fragmented.** Roads with a right-of-way of 60 ft and above went from MCD to the Delhi Govt (PWD) in Jan 2012; narrower roads stay with MCD. Sewage belongs to DJB, sanitation to MCD, and NDMC, DDA, Flood Control and others cover their own zones. [PWD circular 2025][s1], [Business Standard][s2], [IDR][s3]
- **Departments bounce complaints.** "Not my department" is a common reply. Even the civic bodies have publicly disowned potholes on roads that are wider than 60 ft. [The Quint][s4], [HT/PressReader][s5]
- **There is no follow-through.** Complaints go unanswered, and citizens don't know the deadline they are owed or how to escalate.

**Existing channels all assume the citizen already knows where to go:** CPGRAMS (pgportal.gov.in), the Swachhata-MoHUA app, the MCD 311 app / 155305, PWD 1908, DJB 1916, and department X (Twitter) handles. [The Statesman][s6], [ComplaintHub MCD][s7], [ComplaintHub DJB][s8]. A unified 311 helpline was announced in 2025, but we have not confirmed that it is live citywide. [Daily Pioneer][s9], [Tribune][s10]

**Gap we fill:** take a photo, voice note or text in Hindi or English, then work out **the right department, the cited rule, the deadline and a ready-to-file complaint on the correct official channel**, and stay with the citizen through escalation.

> **Overlap note:** the basic layer (a geotagged photo complaint tagged with an authority) overlaps with Swachhata. That is intended, because it is the foundation. What we add is routing with citations, dossiers and citizen-side escalation.

### Key constraint
The app has **no authority over government** and **will NOT auto-submit on government portals**. Portals need OTP and captcha, and impersonating the citizen raises IT Act §66C risk. The app **routes and prepares**. The **citizen files** on the existing channel. Escalation is done by the citizen with the app's help.

---

## 2. Core Value

> "Snap it, say it, and we tell you who owns it, cite the rule that proves it, and hand you the complaint ready to file."

1. **Right door first time:** the routing decision comes with a confidence score.
2. **Proof, not opinion:** every routing decision and deadline cites a source document.
3. **Works for everyone:** voice in Hindi or Hinglish, so users don't need to type or read English.
4. **Follow-through:** deadlines, reminders and drafted appeals.

---

## 3. Target Users / Personas

| Persona | Profile | Pain | What they need from us |
|---|---|---|---|
| **Priya, 28, urban educated** | IT professional in South Delhi, smartphone-native, English/Hinglish | Has filed on MCD 311 and got bounced to PWD; no time to research bylaws | Fast intake (<60s), correct department, an email draft, a deadline tracker |
| **Ramesh, 40, low-literacy daily-wage worker** | Construction labourer, basic Android phone, Hindi speaker, reads little | Can't type complaints and doesn't know any department names | Voice note in Hindi, big buttons, landmark-based location, (Could) Hindi audio readout |
| **Kamla ji, 67, elderly resident** | Retired, lives in a colony lane, uses WhatsApp with help from family | Broken streetlight makes evening walks unsafe; portals confuse her | Simple flow, "me too" join on an existing issue, clear status ("day 5 of 15") |

Secondary: RWA volunteers who report on behalf of a lane (served by the same flows, no special features).

---

## 4. User Journeys

### 4.1 Ganesh-visarjan idols at Kalindi Kunj ghat (garbage dump)
1. After the festival, Priya sees idol debris and plastic dumped at the ghat. She opens the app and taps **Capture** (live camera).
2. The photo is stamped with GPS and time at the shutter. She records: *"Ghat pe visarjan ke baad murtiyan aur plastic pada hai, smell aa rahi hai."*
3. The agent transcribes the voice note, sets **category = garbage dump**, **severity = high** (riverbank, Yamuna) and **confidence = 0.91**.
4. The map pin shows her current location and she confirms it. Ward lookup runs, then the curated rules map the spot to the responsible authority. A riverbank or ghat may involve a different agency than a colony lane, and the rules dataset decides.
5. RAG retrieves the cited rules: the NGT guidelines on idol immersion and the citizen-charter SLA. The dossier shows the department, the official contact, the rule citation, and a **deadline of N days (cited)**.
6. Dedup finds 3 reports within 50m with similar images. Hers is merged into the master issue: **"4 citizens reported"**.
7. She gets a Hindi + English complaint, a deep link to the correct channel and an email draft. **She files it herself.**
8. On day X the tracker reminds her that the deadline has passed and offers a drafted first appeal (Should).

### 4.2 Pothole on a main road
1. Ramesh is on an auto and sees a deep pothole. He records a voice note: *"Bada gaddha hai, sarkari school ke saamne."*
2. GPS is drifting because the auto is moving. The app asks him to **confirm or drag the pin**. He can't read the map well, so the landmark text is geocoded and he confirms.
3. Routing: the road's RoW is ≥60 ft, so it goes to **PWD**, not MCD. The cited rule is the 2012 transfer notification, via the PWD circular.
4. The dossier gives the PWD 1908 / PWD grievance channel deep link and the pre-filled complaint.
5. MCD later tells someone "not ours". Ramesh taps **"Dept rejected"** and the app generates a **Jurisdiction evidence letter** (Should) that cites the ownership rule.

---

## 5. Features (MoSCoW)

### MUST (locked core)

| # | Feature | User problem it solves | Acceptance (summary) |
|---|---|---|---|
| M1 | **Multimodal intake**: photo, voice note and text in Hindi/English/Hinglish. STT, category, severity and confidence. Junk images (selfies, memes, unrelated) are rejected. | Users can't describe or type the issue in "government language" | Outputs category, severity and confidence. If confidence is below threshold, **ask the user, never guess**. Junk images are rejected with a reason. |
| M2 | **Location**: captured in-app at submit (browser geolocation), and the user confirms or drags the pin. Fallback: landmark text is geocoded. **Photo EXIF is not used** (apps strip it). | Users don't know addresses or ward numbers | A confirmed pin is required before routing. The landmark fallback works with no GPS. |
| M3 | **Jurisdiction routing**: pin → ward (boundary lookup) → department + official contact, using a **hand-curated rules dataset (~20 Delhi wards, clearly labelled "curated")**, with a confidence score. | "Which agency owns this spot?" | Returns the department, contact and confidence. Low confidence shows alternatives and the reason. |
| M4 | **Cited rules via RAG** over curated docs (citizen charters, bylaws, NGT guidelines, SLA timelines). | Bouncing and "not my department" | Every routing decision and deadline carries a citation (doc + section). **No uncited claims.** If no source is found, the output says "no cited rule found". |
| M5 | **Complaint dossier**: pre-filled complaint (Hindi + English), photo, location, cited rule, deadline, a deep link to the correct official channel and an email draft to the published grievance address. | Citizens don't know what to write or where to send it | Dossier generated. **User files it.** The app sends nothing to government. |
| M6 | **Dedup**: same category within ~50m plus image similarity → merged into one master issue. New reporters join it ("me too"), and the report count appears in the dossier. | Duplicate noise; a single voice is ignored | Merges duplicates and shows "N citizens reported". |
| M7 | **Evaluation**: ~100 hand-labelled test cases. | Shows the system actually works (portfolio credibility) | Metrics in §7 are reported in an eval report. |
| M8 | **Privacy**: PII encrypted at rest and masked in anything outbound. Data is used only for the complaint. | Fear of exposure or retaliation | No raw phone numbers or names in shared or public outputs. The privacy notice is shown at onboarding. |

### SHOULD (confirmed)

| # | Feature | User problem |
|---|---|---|
| S1 | **Live in-app camera capture** stamps GPS and time at the shutter (tamper-evident, so old gallery photos can't pass as new). Gallery uploads are allowed but flagged **"unverified location"**. | Fake or old photos weaken complaints |
| S2 | **Pre-submit photo quality check**: if the photo is too dark or blurry, the app says "turn on flash / retake". This replaces post-hoc enhancement. | Night photos and blurry shots get rejected by departments |
| S3 | **Ghost-overlay re-shoot for resolution proof**: when someone reports "fixed", the camera shows the original photo semi-transparent so the after-photo matches the angle. Then: AI compare, GPS within ~30m, and a vote by master-issue reporters ("fixed / not fixed"). **The AI result is advisory.** | Departments mark complaints "resolved" when nothing was fixed |
| S4 | **Citizen-side escalation ladder** using RAG deadlines: reminder → auto-drafted first appeal to the dept nodal/PG officer → CPGRAMS complaint draft → RTI application draft (citizen files each one). The tracker shows **"day X of Y"**. | No follow-through, and citizens don't know the next step |
| S5 | **Jurisdiction evidence letter**: when the department rejects a complaint as "not ours", the app generates a reply citing the ownership rule, or re-routes to the correct department with the reason. | Department bouncing |

### COULD (confirmed)

| # | Feature | User problem |
|---|---|---|
| C1 | **Self-improving routing**: the citizen reports the outcome (accepted / bounced to dept X). Repeated corrections flag that rules-dataset entry for human review (no auto-edit). | The curated dataset will have errors |
| C2 | **Public ward map/scoreboard** built only from verifiable data: reported, open age, citizen-confirmed fixed. **No blaming of named officers.** | No transparency on what is still open |
| C3 | **Image vs voice mismatch** (e.g. a streetlight photo with a voice note about garbage) → the agent asks a clarifying question. | Wrong category leads to wrong department |
| C4 | **Hindi audio readout** of status for low-literacy users. | Ramesh can't read status text |
| C5 | **Copy-tweet button**: pre-filled text the user posts manually to the department's X handle. | Public pressure channel |

### WON'T (this version)

- Auto-filing on any government portal (OTP/captcha/IT Act §66C)
- Officer penalties or arbitration verdicts
- Automated tweeting or posting (the manual copy-tweet in C5 is fine)
- Multiple cities, or languages other than Hindi/English
- Material-type detection
- NGO and recycler / circular-economy matching (moved to Future Roadmap)

> **Scope rule:** anything new must map to a row in §3 (personas) or §1 (problem). If it doesn't, it goes to the backlog, not the build.

---

## 6. Core Flow (high level)

```
Capture (photo/voice/text)
   → Intake agent: STT · category · severity · confidence · junk filter
        ↳ unsure? → ask user
   → Location: GPS pin confirm/drag  |  landmark geocode
   → Dedup check (≤50m + same category + image similarity) → join master or create new
   → Routing: ward lookup → curated rules → dept + contact + confidence
   → RAG: cite rule + SLA deadline (no citation → say so)
   → Dossier: HI+EN complaint · photo · pin · citation · deadline · deep link · email draft
   → Citizen files → Tracker (day X of Y) → escalation ladder (Should)
```

---

## 7. Success Metrics

| Metric | Target | How measured |
|---|---|---|
| Routing accuracy (correct department) | **≥ 85%** | ~100 hand-labelled test cases |
| Citation faithfulness | ≥ 0.85 (Ragas faithfulness) | Ragas on the test set |
| Citation context precision / recall | ≥ 0.80 each (Ragas) | Ragas on the test set |
| Junk-image filter precision | ≥ 90% | Labelled junk + valid image set |
| Dedup precision | ≥ 90% | Labelled duplicate pairs |
| Intake → dossier time | **< 60 s** (p90) | Timed end-to-end runs |
| Uncited claims in dossiers | 0 | Automated check: every claim has a source |
| "Ask user" when unsure | 100% below threshold | Low-confidence test cases |

Targets other than routing (≥85%) and time (<60s) are PM proposals. Tune them after the first eval run.

**Test set composition (~100):** about 25 per category. It should cover Hindi, English and Hinglish voice, night photos, junk images, ambiguous PWD/MCD boundary roads, duplicates and landmark-only location.

---

## 8. Edge Cases

| Case | Expected behaviour |
|---|---|
| **GPS drift from a moving vehicle** | Low-accuracy fix → require pin confirm or drag. Offer the landmark fallback. |
| **Night or dark photo** | Pre-submit quality check asks for flash or a retake (S2). If the user submits anyway, mark it low quality and rely on the voice/text. |
| **Image vs voice mismatch** | Ask a clarifying question (C3). Never silently pick one. |
| **Junk image** (selfie, meme, screenshot, unrelated) | Reject with a reason and ask for a relevant photo. |
| **Spam / repeat reporter** | Rate-limit per user. The same user at the same spot counts once in the "me too" count. |
| **Duplicates** | Merge into the master issue, and the report count goes up. |
| **Wrong department / bounced** | Jurisdiction evidence letter or re-route with the reason (S5). Log the outcome (C1). |
| **No response by deadline** | Escalation ladder: reminder → first appeal → CPGRAMS → RTI drafts (S4). |
| **No rule found in corpus** | Say "no cited rule found", lower the confidence and do not invent a deadline. |
| **Location outside the ~20 curated wards** | Say "not covered yet" and show the general channels (MCD 311 / 1908 / 1916 / CPGRAMS) without claiming a routing decision. |
| **Boundary spot (two wards or agencies)** | Show both candidates with confidence. The user picks, or the app asks a clarifying question. |
| **No GPS permission** | Landmark text geocode, then pin confirm. |
| **Gallery upload** | Allowed, flagged "unverified location" (S1). |
| **Unsupported category** (e.g. a stray-dog issue) | Say it's out of scope and point to the general grievance channel. |

---

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Jurisdiction data has no public source** (road RoW, agency per segment) | Routing accuracy | Hand-curated dataset for ~20 wards, **labelled "curated"** in the UI, with a confidence score and the C1 correction loop |
| **Messy or Hindi bylaw PDFs** (scanned, mixed script) | Poor retrieval, wrong citations | Curate a small, clean corpus. OCR plus a manual check on key docs. Measure with Ragas. |
| **Model reliability** (STT on Hinglish, vision misclassification) | Wrong category or department | Confidence thresholds, "ask, don't guess", and the eval set covers the hard cases |
| **Emailing real officials during the demo** | Spam to real government inboxes; reputational/legal risk | **Demo uses a sandbox inbox only.** Real addresses are shown in the dossier but never auto-sent. |
| **Legal: impersonation or auto-filing** | IT Act §66C exposure | No auto-submit. The citizen files every complaint. |
| **Contact info goes stale** (helplines change, unified 311 announced) | Dead-end dossiers | A "last verified" date on every contact. Contacts live in the curated dataset. |
| **Public scoreboard read as defamation** | Reputational risk | Verifiable data only, no named officers (C2) |
| **PII leak** | User harm | Encryption and outbound masking (M8) |

---

## 10. Assumptions

1. Users have a smartphone with a camera, mic and a browser that supports geolocation.
2. The ward boundary data for the ~20 pilot wards is obtainable (public ward maps), even though the agency-per-road data is not.
3. Citizen charters, NGT guidelines and SLA documents are publicly available and can be curated into a small corpus.
4. The official channels (MCD 311/155305, PWD 1908, DJB 1916, CPGRAMS, published grievance emails) stay reachable via deep link or email.
5. Users will do the final filing step themselves.
6. Being a portfolio project, success is measured on the eval set and demo, not on real-world adoption.
7. "Resolved" in the app means citizen-confirmed, not government-confirmed.

---

## 11. Out of Scope Clarifications

- No admin or department-side dashboard (we have no government partner).
- No payments or rewards.
- No social feed or comments beyond "me too" and the fixed/not-fixed vote.

---

## 12. Future Roadmap (post-v1)

- **NGO partner matching**: route issues like garbage and ghat cleanup to registered NGOs (the user wants this later).
- **Recycler / circular-economy matching**, including material-type detection.
- More Delhi wards, then other cities with their own rules datasets.
- More languages (Punjabi, Urdu, etc.).
- Official department integrations, only through formal APIs and partnerships.
- Phased detail lives in `docs/ROADMAP.md`.

---

## 13. Decisions (confirmed 2026-10-24)

1. Should/Could list S1–S5, C1–C5: **accepted**.
2. Wards: ~20, a mix including PWD/MCD boundary roads and the Kalindi Kunj Yamuna-ghat area (exact list in REQUIREMENTS.md).
3. "Ask user" confidence threshold: **0.7**.
4. Dedup radius **50 m**; resolution GPS radius **30 m** (tunable after eval).

---

## 14. Weak-Spot Fixes (added 2026-10-24, override earlier text where they conflict)

| # | Weak spot | Fix | Changes |
|---|---|---|---|
| W1 | **Circular eval**: rules and test labels come from the same person and the same table | **Blind labelling**: test-set answers come from official sources (PWD notified road list, NGT/charter docs, news reports of which agency fixed what), labelled by a second person who has not seen the rules table. The test runs **end-to-end from raw photo + voice + pin**, and each stage is reported separately (intake, location, routing, citation). | M7 |
| W2 | **Ward is not enough**: the department depends on the road (≥60 ft RoW = PWD), not the ward | **Road-level routing**: snap the pin to the nearest road segment (OSM) and match it against the PWD notified road list, plus special-zone polygons (Yamuna floodplain/ghats, NDMC, Cantonment). **Multimodal tie-break**: the image model judges "wide multi-lane road vs narrow gali". If still unsure, ask one tap: "Main road or inner lane?" Boundary spots are treated as normal, not as an edge case. | M3 |
| W3 | **Last step is impossible for low-literacy users** (portal/email) | **Call mode (S6)**: one tap dials the right helpline (1908 / 1916 / 311 / 155305), and the app shows and reads out a short Hindi script with the complaint, location and rule. **Helper share**: send the dossier link to a family member or RWA. **Printable dossier (PDF + QR to the photo evidence)** to hand in at the ward office. | new S6 |
| W4 | **Users don't come back**, so the tracker, escalation, votes and C1 starve | **Check-in nudges (S7)**: web push at day 3 / 7 / deadline with one-tap answers ("Did anyone come? Yes / No / Fixed"). Any reporter on the master issue can update it. **Passers-by confirm**: nearby users see "Is this still here?" Unknown stays **"status unknown"**, never auto-"resolved". | new S7 |
| W5 | **Vote breaks with a single reporter** | 1 reporter: their after-photo + 30 m check + AI score (advisory). 2 reporters: both agree, or 1 + AI. 3 or more: majority within 7 days. No answer in 14 days → "status unknown". | S3 |
| W6 | **Often no cited deadline exists** | Show **"Suggested deadline (no official rule found)"**, clearly labelled. The escalation ladder anchors on the legal timelines that do exist (RTI Act: 30-day reply; CPGRAMS redressal norm, to verify). | M4, S4 |
| W7 | **Scope: 20 features** | **Strict build order**: Phase 1 = Must (demo-able core) → Phase 2 = Should → Phase 3 = Could if time allows. The public map uses seeded data labelled "sample". | all |

---

## Handoff

- **business-analyst** → `docs/REQUIREMENTS.md`: detailed functional requirements, curated dataset schema, threshold values, eval set spec.
- **ux-designer** → `docs/UX.md`: intake flow, pin confirm, dossier screen, tracker/escalation, low-literacy and elderly accessibility.

---

## Sources

[s1]: https://pwddelhi.gov.in/writeread/Circular\Cir_202502271545457660.pdf
[s2]: https://www.business-standard.com/article/pti-stories/roads-wider-than-60-ft-should-be-given-back-to-corp-from-pwd-114063001162_1.html
[s3]: https://idronline.org/wp-content/uploads/2025/01/Governance-Breakdown.pdf
[s4]: https://thequint.com/news/india/delhi-traffic-police-finds-167-potholes-mcds-deny-responsibility
[s5]: https://www.pressreader.com/india/hindustan-times-gurugram/20170328/282505773432846
[s6]: https://www.thestatesman.com/cities/delhi-govt-introduces-4-digit-helpline-numbers-for-pwd-and-water-board-complaints-1503410773.html
[s7]: https://complainthub.org/mcd-help/
[s8]: https://complainthub.org/delhi-jal-board-help/
[s9]: https://www.dailypioneer.com/2025/state-editions/delhi-to-get-single-number-for-waterlogging-issues.html
[s10]: https://www.tribuneindia.com/news/delhi/city-to-get-unified-helpline-311-for-all-civic-complaints/amp/?utm=relatedarticles

- [s1] PWD Delhi circular (Feb 2025): 60 ft+ RoW roads transferred from MCD, Jan 2012
- [s2] Business Standard: roads wider than 60 ft, PWD vs corporations
- [s3] IDR, "Governance Breakdown": PWD roads above 60 ft, MCD sanitation
- [s4] The Quint: 167 potholes found, MCDs deny responsibility
- [s5] Hindustan Times (PressReader): MCD road record, jurisdiction confusion
- [s6] The Statesman: 1908 (PWD) and 1916 (DJB) helplines
- [s7] ComplaintHub: MCD 311 app / 155305 / PGMS escalation
- [s8] ComplaintHub: DJB 1916 / grievance email
- [s9] Daily Pioneer: single number for waterlogging (2025)
- [s10] Tribune: unified 311 helpline announced (live status unverified)
- Also referenced: CPGRAMS (pgportal.gov.in), Swachhata-MoHUA app
