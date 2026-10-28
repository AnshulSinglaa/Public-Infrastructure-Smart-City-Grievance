# REQUIREMENTS: Civic Grievance Router

| | |
|---|---|
| **Source** | `docs/PRD.md` (v1 confirmed 2026-10-24: M1–M8, S1–S5, C1–C5) · `docs/TECH-MEETING-01.md` (all decisions accepted by the user 2026-10-24) |
| **Author** | Business Analyst |
| **Consumers** | tech-lead → ARCHITECTURE.md · qa-engineer → test cases · ux-designer (UX.md, in parallel) |
| **Scope rule** | This doc covers behaviour, data and rules only. It does not design screens. Technology names appear only where a TECH-MEETING-01 decision fixed them. |

**Fixed values (from PRD §13):** ask-user confidence threshold **0.7** · dedup radius **50 m** · resolution GPS radius **30 m**. All three are tunable after the first eval run, **on the dev set only** (§11).

**Values set by TECH-MEETING-01 §3** (the former `⟨TBD-ARCH⟩` placeholders): 3 photos per report (compressed to ≤ 1280 px on upload) · image similarity ≥ 0.85 · road snap 25 m · boundary buffer 15 m · resolved masters matchable for 30 days · "location may be off" = accuracy > 50 m OR speed > 3 m/s OR two fixes 3 s apart differ by > 30 m · photo too dark = mean brightness < 60/255, blurry = Laplacian variance < 100 · resolution distance check = distance − GPS accuracy ≤ 30 m · face/plate blur fails closed · routing-confidence factors recalibrated on the dev set. All numeric values are tuned on the dev set, never the test set.

**PRD §14 weak-spot fixes W1–W7 are incorporated and override earlier text where they conflict.** They add S6 (Call mode) and S7 (Check-in nudges). **TECH-MEETING-01 §4 resolutions Q-1…Q-20 are incorporated** and override earlier text; they are cited inline as "(TM Q-n)".

**Phase tags (W7):** every FR group carries a phase tag. **[P1]** = Must, the demo-able core, built first. **[P2]** = Should. **[P3]** = Could, if time allows. A row inherits its group's tag unless it carries its own. Acceptance blocks are split by phase (TM Q-15); a P1 block never tests P2/P3 behaviour. Nothing from P2 starts before P1 passes its acceptance; the same goes for P3 after P2. **S6 Call mode (FR-13.1–13.3) is P1** (TM Q-16).

**Connectivity (TM Q-9):** v1 is **online-only**. It is not installable and has no offline mode. Unsubmitted drafts are kept in the user's server-side session (Streamlit session state) and resumed within that session (FR-1.11).

**Frontend change (TM-01 "Change: frontend = Streamlit", 2026-10-24):** the UI is a thin Streamlit app over the same FastAPI backend. This replaces the React PWA and **overrides earlier text**: there is no web push (pending check-in cards plus an optional reminder email to the citizen instead), no live ghost-overlay camera (a reference photo with a "same angle" hint instead), geolocation comes from a JS component plus a click-to-confirm map pin, Hindi audio is generated on the server, and drafts are kept in the session.

**Conventions:** FR-IDs map to PRD feature IDs. "User" means the citizen. The app **never** sends anything to a government system. "Citation" means doc + section/page from the curated corpus.

---

## 1. Roles & Permissions

| Role | Who | Can | Cannot |
|---|---|---|---|
| **Citizen** | Any signed-up user (email OTP or Google sign-in) | Create reports, join ("me too"), view own reports + the master issues they joined, record outcomes, vote on resolution of issues they reported (if in the frozen voter set, FR-9.4), delete own account/data | See other users' PII, edit others' reports, vote on issues they didn't report |
| **Curator** (project team) | Dataset maintainer | Edit rules, contacts, ward list, corpus; resolve C1 review flags | View decrypted user PII (except via audited support action, see NFR-SEC-4); label the eval test set (FR-15.2) |
| **Public visitor** (C2 only) | Unauthenticated | View ward scoreboard aggregates | See individual reports, photos with faces/plates, or any PII |
| **System** | Background jobs | Compute deadlines (on read), create pending check-in cards, send optional reminder emails **to the citizen only** (sandbox in the demo), run dedup, flag rules, purge expired data | Send anything to government inboxes/portals |

---

## 2. User Stories

| ID | Story | PRD |
|---|---|---|
| US-1 | As Ramesh, I want to report by voice in Hindi so that I don't have to type or know department names. | M1 |
| US-2 | As Priya, I want the app to tell me which department owns this spot, citing the rule, so that I don't get bounced. | M3, M4 |
| US-3 | As any citizen, I want a ready-to-file complaint and the correct channel link so that filing takes one step. | M5 |
| US-4 | As Kamla ji, I want to join an existing issue with "me too" so that I don't repeat what neighbours already filed. | M6 |
| US-5 | As a citizen, I want my name and phone hidden from anything shared so that I'm safe from retaliation. | M8 |
| US-6 | As a citizen, I want to see "day X of Y" and get the next escalation draft when the deadline passes so that I know what to do next. | S4 |
| US-7 | As Ramesh, when a department says "not ours", I want a letter citing the ownership rule so that I can push back. | S5 |
| US-8 | As a reporter, I want to confirm whether the issue is actually fixed so that fake "resolved" closures don't count. | S3 |
| US-9 | As a citizen, I want to be told to retake a dark/blurry photo before submitting so that my complaint isn't rejected. | S2 |
| US-10 | As a curator, I want repeated citizen corrections to flag a rule for review so that dataset errors get fixed. | C1 |
| US-11 | As Ramesh, I want my status read out in Hindi so that I don't need to read. | C4 |
| US-12 | As the project owner, I want an eval report on 100 blind-labelled test cases so that I can show the system works. | M7 |
| US-13 | As Ramesh, I want to call the right helpline with a Hindi script read out to me so that I can file without a portal or email. | S6 (P1) |
| US-14 | As Kamla ji, I want to send my dossier to my son or RWA, or print it for the ward office, so that someone can file it for me. | S6 (P2) |
| US-15 | As a reporter, I want a one-tap check-in on day 3/7/deadline so that I can update the status without opening the tracker. | S7 |
| US-16 | As a passer-by, I want to answer "Is this still here?" so that old issues don't go stale. | S7 |
| US-17 | As a citizen who got a dossier but hasn't filed, I want a "Did you file it?" reminder so that my complaint doesn't sit unfiled. | M5 (TM Q-13) |
| US-18 | As a citizen whose submit failed mid-report, I want my draft kept for the session so that I can retry without starting over. | M1 (TM Q-9) |

---

## 3. Functional Requirements

### FR-1 Multimodal intake (M1, S1, S2, C3) [P1]

| ID | Requirement |
|---|---|
| FR-1.1 | Accept any combination of: ≥0 photos (max **3**, compressed to ≤ 1280 px on the long side on upload), ≤1 voice note (max 2 min), free text (max 1000 chars). Reject a report with none of the three. A photo is **optional** but strongly prompted. |
| FR-1.2 | Transcribe voice in Hindi, English or Hinglish. Store the transcript with the detected language. |
| FR-1.3 | Output `category ∈ {pothole, garbage_dump, waterlogging_sewage, streetlight}` or `out_of_scope`, `severity ∈ {low, medium, high}`, `confidence ∈ [0,1]`. |
| FR-1.4 | **Ask, don't guess:** if confidence < 0.7, set state `needs_clarification` and ask the user to pick the category (show the top-2 candidates + "something else"). Never auto-pick. |
| FR-1.5 | Junk filter: reject selfies, memes, screenshots, documents and unrelated scenes **with a reason**, then ask for a relevant photo. If the text/voice alone is valid, the user may continue without a photo. |
| FR-1.6 | `out_of_scope` category (e.g. stray dogs): tell the user it's out of scope and show the general channels (MCD 311/155305, CPGRAMS). No routing. |
| FR-1.10 (TM Q-7) | **Drain type:** for `waterlogging_sewage`, record `drain_type ∈ {sewer_manhole, open_nala, unknown}` with a confidence. The image judgement tags manhole/sewer line vs open nala; voice/text cues count too. `unknown` or confidence < 0.7 → the drain question in FR-3.7. `drain_type` feeds the asset condition in routing (§7.1 step 3: sewer → DJB; open nala → drain owner). |
| FR-1.11 (TM Q-9, Streamlit) | **Online-only v1, session draft:** submission requires connectivity. The in-progress draft (inputs, captured fixes, answers so far) is kept in the **Streamlit session state** as the user goes. If a call to the backend fails, show "Couldn't reach the server, your draft is kept" and a manual **Retry/Resume** action; nothing is queued or auto-submitted. A session draft does not survive the end of the session (see Q20). Not installable, no offline mode. |
| FR-1.7 (S1) [P2] | In-app camera stamps GPS + device time at the shutter into the report record (not EXIF). Gallery uploads are accepted and flagged `location_verified = false` ("unverified location") in the dossier. |
| FR-1.8 (S2) [P2] | Before submit (on upload), check each photo: **too dark** = mean brightness < 60/255; **blurry** = Laplacian variance < 100. On failure, show "turn on flash / retake". The user may override; then mark `photo_quality = low` and weight voice/text in classification. |
| FR-1.9 (C3) [P3] | If image category ≠ voice/text category (both individually ≥ 0.7), ask a clarifying question naming both. Never silently pick one. |

There is no volume-key shutter (TM Q-19); the shutter is an on-screen button.

**Acceptance [P1]**
- Given a Hindi voice note "bada gaddha hai" and a pothole photo, when intake runs, then category = pothole with confidence ≥ 0.7 and the transcript is stored.
- Given any input where confidence < 0.7, when intake runs, then the user is asked to choose and no routing happens until they answer (100% of eval low-confidence cases).
- Given a selfie, when intake runs, then it is rejected with a human-readable reason and the report is not created from that image.
- Given 4 photos, then the 4th is refused; stored photos are ≤ 1280 px on the long side.
- Given a waterlogging photo showing an open manhole, then `drain_type = sewer_manhole`; given an `unknown` drain type, then the drain question (FR-3.7) is asked.
- Given a backend call fails mid-draft, when the user taps Retry in the same session, then the draft resumes with all inputs intact, and nothing was submitted.

**Acceptance [P2]**
- Given a photo with mean brightness 50/255 (or Laplacian variance 80), when the user taps submit, then the retake prompt appears; if overridden, `photo_quality = low` is saved.
- Given a gallery photo, then the dossier shows "unverified location".

**Acceptance [P3]**
- Given a streetlight photo + a voice note about garbage, then a clarifying question is shown (C3).

### FR-2 Location (M2) [P1]

| ID | Requirement |
|---|---|
| FR-2.1 | Use browser location only, read through a small JS geolocation component. Do **not** read photo EXIF. **Pin seed (TM Q-20):** the proposed pin is the **best-accuracy fix received since the camera opened** (or since intake opened, if no in-app photo is taken). It is shown on a map, and the user **clicks to confirm or move the pin** (FR-2.2). |
| FR-2.2 | A **confirmed pin** (user tapped confirm, optionally after dragging) is required before dedup/routing. |
| FR-2.3 | **"Location may be off"** if any of: reported accuracy > 50 m, speed > 3 m/s, or two fixes 3 s apart differ by > 30 m. Show the prompt and offer the landmark fallback. |
| FR-2.4 | No GPS permission / no fix: accept landmark text (Hindi or English, e.g. "sarkari school ke saamne, Jaitpur"), geocode it to a candidate pin, and require confirmation. |
| FR-2.5 | Store `pin_source ∈ {gps, gps_dragged, landmark}`, the seed fix (lat, lng, accuracy, ts), and the drag distance from the seed. |

**Acceptance [P1]**
- Given GPS is denied, when the user types a landmark, then a pin is proposed and routing waits for confirmation.
- Given a pin that is never confirmed, then no routing or dossier is produced.
- Given a photo with EXIF GPS, then the EXIF values are ignored (verified by a test photo whose EXIF points elsewhere).
- Given fixes of 40 m, 12 m and 25 m accuracy after the camera opened, then the pin is seeded from the 12 m fix.
- Given accuracy 60 m, or speed 4 m/s, or two fixes 3 s apart 35 m apart, then "location may be off" is shown.

### FR-3 Jurisdiction routing: road-level (M3, S5, W2) [P1]

The **road, not the ward**, decides the department (W2). The ward is used for coverage, the contact scope and the scoreboard. Routing is deterministic (no model picks the department, TM §1).

| ID | Requirement |
|---|---|
| FR-3.1 | Pin → ward via boundary lookup (point-in-polygon on curated ward boundaries). |
| FR-3.2 | Ward not in the curated set → state `out_of_coverage`: show "not covered yet" plus general channels. **Make no routing claim.** |
| FR-3.3 | **Special-zone check first:** if the pin is inside a curated SpecialZone polygon (Yamuna floodplain/ghats, NDMC area, Cantonment, DDA land), the zone's rule applies (§7.1 step 1). |
| FR-3.4 | **Road snap:** snap the pin to the nearest OSM road segment within **25 m** (one value everywhere). Record segment id, OSM highway class, name, and snap distance. No segment within range → `road = none` (open plot/park/ghat → asset-type rules). |
| FR-3.5 | **PWD list match:** match the snapped segment against the curated **PWD notified road list** (by name + geometry overlap). Matched → `row_class = ≥60ft`, `row_source = pwd_list` (cited). Not matched → `row_class = unknown` (not automatically <60 ft). |
| FR-3.6 | **Multimodal tie-break:** if `row_class = unknown`, or two segments are within the **15 m boundary buffer** of the pin (a junction or a service lane beside a main road), use the image judgement `wide_multilane / narrow_gali / unclear` plus the OSM class as evidence. Confidence ≥ 0.7 → use it, with `row_source = inferred`. The same 15 m buffer applies at ward edges. |
| FR-3.7 | **One-tap question (TM Q-6):** still unsure (routing confidence < 0.7, image `unclear`, or no photo) → ask exactly **one** question **matched to the issue type**, with two big options + "Not sure": **road** (pothole/streetlight): "Main road or inner lane? / मेन रोड या अंदर की गली?"; **drain** (waterlogging_sewage): "Big open nala or sewer line/manhole? / बड़ा खुला नाला या सीवर लाइन/मैनहोल?"; **ghat** (special zone ghat/floodplain): "On the river bank or in the water? / नदी के किनारे या पानी में?". "Not sure" → show both candidate departments with confidences; the user picks. |
| FR-3.8 | Evaluate RoutingRules (§7.1) with zone + row_class + category + asset (incl. `drain_type`) → department, contact, rule ID, and `routing_confidence = rule.confidence × evidence × citation × staleness`. **All factors are recalibrated on the dev set** (TM Q-6) so that well-evidenced cases don't always ask. Starting values: evidence pwd_list 1.0 · user_answer 0.95 · inferred 0.8; citation ×0.8 when no cited rule (FR-4.3); staleness ×0.9 when `last_verified` > 12 months (§9). The calibrated values are recorded in config with the dev-set version. |
| FR-3.9 | Boundary spots (ward edge, road junction, main road vs service lane) go through FR-3.6–3.7 as the **normal path**, not as an error. |
| FR-3.10 | Every routing output is labelled "curated data", shows the evidence used (PWD list / image / your answer), and the contact's `last_verified` date. |
| FR-3.11 (S5) [P2] | User taps "Dept rejected" → the report moves to `bounced`. If the rule says the issue **is** that department's, the **jurisdiction evidence letter** (HI+EN, citing the ownership rule) is offered **first**. Otherwise (or if the user prefers) **re-route** to another department with a reason, if the rules give a credible alternative. **Re-route restarts the master's clock for the new department** (new `clock_started_at` = re-route date, new SLA); earlier escalation steps and the old department stay in history and are not carried over (TM Q-11). |

**Acceptance [P1]**
- Given a pin 10 m from a road on the PWD notified list, category = pothole, then department = PWD, `row_source = pwd_list`, with a citation to the 2012 transfer notification/PWD circular.
- Given a pin on an unlisted residential OSM segment and a photo judged `narrow_gali` (≥ 0.7), then department = MCD with evidence "image".
- Given an unlisted segment and an `unclear` image, then exactly one "Main road or inner lane?" question is asked; "Inner lane" → MCD.
- Given a waterlogging report with `drain_type = unknown`, then the question asked is the nala/sewer one (never the road one); "Sewer line/manhole" → DJB.
- Given a ghat-zone report below threshold, then the question asked is "On the river bank or in the water?" (never the road one).
- Given a pin on a service lane 8 m from a PWD-listed main road, then the tie-break runs (FR-3.6) and, if still unsure, the one-tap question is asked.
- Given a pin 30 m from the nearest road, then `road = none`.
- Given a pin inside the Yamuna floodplain/ghat polygon, then the zone rule applies regardless of the nearest road.
- Given a pin outside the covered wards, then no department is asserted and the general channels are listed.

**Acceptance [P2]**
- Given "Dept rejected" on a PWD-routed pothole whose rule says PWD, then the evidence letter is offered first and cites the same rule as the dossier.
- Given a re-route from PWD to DJB on day 10, then the tracker shows "day 0 of Y(DJB)", and the PWD period and any L1 step appear in history only.

### FR-4 Cited rules via RAG (M4, W6) [P1]

| ID | Requirement |
|---|---|
| FR-4.1 | Retrieve from the curated PolicyDoc corpus only (§5.3). |
| FR-4.2 | Every routing decision and every deadline carries ≥1 Citation (doc title, section/clause or page, and the quoted span). Generated claims are structured `{claim, citation_id}` pairs, so uncited text cannot be emitted (TM §1). |
| FR-4.3 | No source found → the output text is exactly "no cited rule found" (HI: "कोई उद्धृत नियम नहीं मिला"), routing confidence takes the citation factor (starting ×0.8, FR-3.8), and **no deadline is presented as official**. |
| FR-4.6 (W6, TM Q-4) | No cited SLA for that department + category → **always show** a suggested deadline (never omit it, including in the no-rule flow), labelled **"Suggested deadline (no official rule found)" / "सुझाई गई समय-सीमा (कोई आधिकारिक नियम नहीं मिला)"**, using the default from FR-4.7. The Hindi phrase is exactly **"कोई आधिकारिक नियम नहीं मिला"** everywhere; no variant (e.g. "सरकारी") is allowed. The label appears wherever that date appears (dossier, tracker, drafts, call script, readout). Drafts word it as "we request action within N days", never as "you are required by rule to". |
| FR-4.7 (TM Q-15) | **Suggested-wait config (P1):** one config holds the suggested values: **15 days** default SLA (used by FR-4.6 from P1), 15 days L1 wait, 30 days L2 wait (used by §6 from P2). Values are never presented as official. |
| FR-4.4 | Automated post-check: every factual claim sentence in a dossier/letter maps to a Citation, or it is removed. Target: 0 uncited claims. |
| FR-4.5 | A citation's quoted span must exist verbatim (or OCR-normalised) in the cited chunk. |

**Acceptance [P1]**
- Given any generated dossier, when the claim checker runs, then the uncited claim count = 0.
- Given a query with no matching corpus content, then "no cited rule found" appears and a 15-day deadline carries the label "Suggested deadline (no official rule found)" in every place it is shown.
- Given any Hindi output with a suggested deadline, then it contains "कोई आधिकारिक नियम नहीं मिला" and never "सरकारी नियम".

### FR-5 Complaint dossier (M5, C5) [P1]

| ID | Requirement |
|---|---|
| FR-5.1 | Dossier contains: complaint text in Hindi **and** English; photo(s) (blurred per FR-8.4); pin (lat/long + ward + landmark text); category + severity; department + contact; citation(s); deadline (date, SLA days, cited or "Suggested deadline (no official rule found)" label); "N citizens reported"; deep link to the official channel; email draft to the published grievance address; location-verified flag. |
| FR-5.2 (TM Q-19) | **Email:** the mailto link fills the **subject only** (plus the recipient); a separate **"Copy body"** button copies the full bilingual body to the clipboard for pasting. **The app never sends it.** |
| FR-5.3 | Complaint text uses the PII masking rules (FR-8.3). It contains the reporter's name/phone only if the user opts in for that one filing, because portals need it. |
| FR-5.4 (TM Q-10) | The user marks "I filed it" + channel + optional reference number + filed date → state `filed`. **The master's clock starts at its first "filed" date** (`clock_started_at`); reporters who join or file later inherit that clock. Each report still stores its own filed date and ref no. |
| FR-5.7 (TM Q-13) | **"Did you file it?" nudge:** a report in `dossier_ready` gets a reminder at **day 1 and day 3** after the dossier was generated, with one-tap **Yes, I filed it** (→ FR-5.4 form) / **Not yet** / **Help me file** (→ Call mode / share). Shown as a **pending check-in card on login and on the home screen** (P1), plus, from P2, the optional reminder email **to the citizen** (FR-14.7; sandbox inbox in the demo), under the FR-14.3 limits. No web push. No further nudges after day 3. |
| FR-5.5 (C5) [P3] | "Copy tweet" puts prefilled text (≤280 chars, masked, with the department's X handle) on the clipboard. No posting. |
| FR-5.6 | The dossier is viewable at a shareable link (unguessable token, masked). The printable PDF with a QR code is FR-13.4 [P2]. |

**Acceptance [P1]**
- Given a routed report, then all FR-5.1 fields are present, both languages are present, and the time from intake to dossier is < 60 s p90.
- Given the demo environment, then any test email goes only to the sandbox inbox (NFR-DEMO-1).
- Given a dossier, then no raw phone number or full name of any user appears unless the opt-in is set.
- Given the mailto link, then it carries the subject only; "Copy body" puts the full body on the clipboard.
- Given A files on day 0 and B joins and files on day 5, then the master shows "day 5 of Y" for both.
- Given a dossier not marked filed, then "Did you file it?" appears on day 1 and day 3, and not after.

**Acceptance [P3]**
- Given "Copy tweet", then the clipboard text is ≤ 280 chars and masked.

### FR-6 Dedup / master issue (M6) [P1]

| ID | Requirement |
|---|---|
| FR-6.1 | Candidate duplicate = same category **and** distance ≤ 50 m **and** (the master is open, or `resolved` ≤ 30 days ago, FR-6.6) **and** image similarity ≥ **0.85** (starting value, tuned on the dev set). Similarity only ranks candidates; the **user always confirms the join**. |
| FR-6.2 | If there is no photo on either side, match on category + ≤ 50 m only, then **ask** the user "Is this the same issue? (show the master photo)". |
| FR-6.7 (TM Q-14) | **Low-quality photo fallback:** if the new photo is low quality (`photo_quality = low`, or it fails the FR-1.8 thresholds, or no usable image embedding), skip the image-similarity gate and show a candidate list by **location + category** (≤ 50 m), nearest first; the user confirms one or picks "None of these". |
| FR-6.3 | Multiple candidates → order by distance (nearest first); the user may decline ("not the same"), which creates a new master. |
| FR-6.4 | A joined report links to the MasterIssue. The "N citizens reported" count = **distinct users** in the master. The same user joining again does not increase N. |
| FR-6.5 | Joining a master that is already `filed` shows its existing dossier + deadline (the master clock, FR-5.4). The new reporter may file their own complaint too (same dossier content, updated N). |
| FR-6.6 (TM Q-8) | A master `resolved` **≤ 30 days** ago stays matchable. It is shown as "Fixed on DD-MM. Still a problem?" with **Reopen?**: Reopen → the report joins, the master → `reopened` (§4); "Different issue" → a new master. A master resolved **> 30 days** ago is not matched; a new master is created with `previous_master_id` pointing to it (if same category ≤ 50 m). |

**Acceptance [P1]**
- Given 3 existing pothole reports within 40 m with similar images, when a 4th distinct user reports and confirms, then they join and the dossier shows "4 citizens reported".
- Given the same user reporting the same spot twice, then N is unchanged.
- Given a garbage report 30 m from a pothole master, then no merge happens.
- Given a pothole 60 m away, then no merge happens.
- Given a pothole master resolved 10 days ago and a new pothole report 20 m away, then "Reopen?" is offered; given one resolved 40 days ago, then a new master is created with `previous_master_id`.
- Given a dark photo 20 m from a pothole master, then the location + category candidate list is shown and no join happens without the user's confirmation.
- Given a similarity of 0.90, then the join is still only proposed, never automatic.

### FR-7 Tracker & escalation ladder (S4, W6) [P2]

| ID | Requirement |
|---|---|
| FR-7.1 | After `filed`, show "day X of Y" where X counts from the master's `clock_started_at` (FR-5.4) and Y = the cited SLA days for that department + category. Computed on read. |
| FR-7.2 | No cited SLA → Y = the suggested 15 days from FR-4.7, labelled "Suggested deadline (no official rule found)" (FR-4.6). |
| FR-7.6 (W6) | The ladder anchors on the legal timelines that do exist: the RTI 30-day reply (RTI Act s.7(1)) and the CPGRAMS redressal norm (**to verify** the current published figure; until it is verified, treat it as suggested). Suggested waits are used only where no legal timeline exists. |
| FR-7.3 | On day Y+1 → `overdue`; in-app reminder offering step L1. Each step is a citizen-filed draft; the app records "filed" per step. |
| FR-7.4 | Ladder (§6): L1 first appeal to the nodal/PG officer (unlocks together with L0) → L2 CPGRAMS draft → L3 RTI draft. L2 and L3 unlock only after the previous step was marked filed **and** its wait period elapsed without resolution. |
| FR-7.5 | The user may resolve/stop escalation at any time. |
| FR-7.7 (TM Q-11) | After a re-route (FR-3.11), the clock and ladder restart for the new department; the old department's steps remain visible as history. |

**Acceptance [P2]**
- Given filed on day 0 with a cited SLA of 7 days, when day 8 arrives with no resolution, then state = overdue and the L1 draft is offered with the citation.
- Given no cited SLA, then the tracker shows "day X of 15" with the label "Suggested deadline (no official rule found)".
- Given L3 is filed, then the tracker shows "RTI reply due by filed+30 days" citing the RTI Act s.7(1).

### FR-8 Privacy (M8) [P1]

| ID | Requirement |
|---|---|
| FR-8.1 | A privacy notice is shown at onboarding and must be accepted before the first report. |
| FR-8.2 | PII (name, phone, email, precise home address if given) encrypted at rest, with a keyed hash for email/phone lookup; see NFR-SEC. |
| FR-8.3 | Outbound masking: phone → `98XXXXXX12`; name → first name + initial, or "A resident"; emails masked; other reporters are never named, only counted. |
| FR-8.6 (TM Q-17) | **Normalise before masking:** before the phone/email detectors run, normalise Devanagari digits (०–९ → 0–9) and spoken number words in Hindi, English and Hinglish (e.g. "नौ आठ", "nau aath", "double five", "डबल पाँच") into digits, so numbers given in those forms are masked too. Masking is applied to the original text at the matched spans. |
| FR-8.4 | Faces and vehicle number plates in photos are blurred (Gaussian blur on detected boxes) before the photo appears in any shared output or C2. **Fails closed:** if detection or blurring errors, the photo is left out of shared outputs only when blur processing errors; a photo with no detected faces/plates is shared as normal (Q16 closed). |
| FR-8.5 | Account deletion removes PII and voice recordings within 7 days; reports become anonymous contributions (the master count stays). |

**Acceptance [P1]**
- Given any dossier, letter, call script or tweet output, when scanned with a phone/email regex after FR-8.6 normalisation, then there are 0 unmasked matches (unless opt-in for that filing).
- Given the text "मेरा नंबर ९८७६५४३२१० है" or "nau aath saat chhe…", then the number is masked in every output.
- Given a DB dump, then PII fields are not readable as plaintext.
- Given the blur step throws an error, then that photo does not appear in the dossier link.

### FR-9 Resolution verification (S3, W5) [P2]

| ID | Requirement |
|---|---|
| FR-9.1 (Streamlit) | Any reporter of the master can tap "Fixed?" → the **original photo is shown above the in-app camera** as a reference, with the hint "Stand where this was taken; same angle / वही जगह, वही कोण". No live ghost overlay. |
| FR-9.2 (TM Q-3) | The after-photo is accepted only if taken in-app with **distance − GPS accuracy ≤ 30 m** from the master pin. Otherwise it is **rejected** with "Move closer" and the distance. Gallery photos are not allowed for resolution. |
| FR-9.3 | AI compare (before vs after) gives an advisory score: `looks_fixed / looks_not_fixed / unclear`. Shown to voters, **never decisive and never counted as a vote** (TM Q-1). |
| FR-9.4 (TM Q-2) | State → `resolved_pending_vote`. **R (the number of distinct reporters) and the voter set are frozen when the after-photo is uploaded**; reporters who join later can see the vote but not vote in it. All frozen voters are notified (a pending vote card on login/home, plus the optional reminder email, FR-14.7) and can vote fixed / not fixed. The after-photo submitter's submission counts as their "fixed" vote. |
| FR-9.5 (TM Q-1) | **Vote rule (W5), by frozen R:** **R = 1:** resolved when that reporter's valid after-photo is submitted. The AI score is shown and stored but does not block. **R = 2:** resolved only if **both reporters vote fixed**. Any not-fixed vote → reopened. The AI never substitutes for a vote. **R ≥ 3:** resolved if the fixed votes are a strict majority of votes cast (minimum 2 fixed votes). Not a majority or a tie → reopened. |
| FR-9.6 | Early close: if all frozen voters have voted, decide immediately. |
| FR-9.7 (TM Q-2) | **Vote window:** the rule is evaluated at **day 7** after the after-photo. If it can't be decided yet (too few votes: R = 2 with only the submitter's vote; R ≥ 3 with fewer than 2 votes cast), keep waiting and decide as soon as a vote makes it decidable. If still undecidable on **day 14** → `status_unknown`. **Never auto-resolve.** |
| FR-9.8 | Passer-by "still here?" answers (FR-14.4) are shown as context but are not votes. |

**Acceptance [P2]**
- Given "Fixed?", then the original photo and the "same angle" hint are shown above the camera.
- Given an after-photo 45 m from the pin with 5 m accuracy (45 − 5 = 40 > 30), then it is rejected with "Move closer".
- Given an after-photo 38 m from the pin with 10 m accuracy (28 ≤ 30), then it is accepted.
- Given R = 1 and a valid after-photo with AI = looks_not_fixed, then resolved (AI advisory); the AI score is visible on the issue.
- Given R = 2, 1 fixed, AI = looks_fixed, then **not** resolved; with no second vote by day 14, then status_unknown.
- Given R = 2 and both vote fixed, then resolved. Given R = 2, 1 fixed + 1 not fixed, then reopened.
- Given R = 2 at upload and a 3rd reporter joins on day 2, then R stays 2 and the new reporter cannot vote.
- Given R = 5, 3 fixed / 1 not fixed by day 7, then resolved. Given R = 4, 2/2, then reopened.
- Given R = 4, only the submitter's vote at day 7 and a 2nd fixed vote on day 10, then resolved on day 10.
- Given R = 4 and only the submitter's vote by day 14, then status_unknown (not resolved).

### FR-10 Outcome feedback & rule review (C1) [P3]

| ID | Requirement |
|---|---|
| FR-10.1 | After filing, the user can record an outcome: `accepted`, `bounced_to(dept)`, `no_response`, `resolved`. |
| FR-10.2 (TM Q-12) | If `bounced_to` the **same other department** is reported on ≥ 3 **distinct masters** for the same RoutingRule within 60 days, flag the rule `needs_review`. Several reporters on one master count once. Only a curator edits; there is no auto-edit. |
| FR-10.3 | Rules under `needs_review` still route, but their confidence is shown with a "being reviewed" note. |

**Acceptance [P3]**
- Given 3 co-reporters of one master all report `bounced_to DJB`, then the rule is **not** flagged; given 3 different masters each with one such report, then it is.

### FR-11 Public ward scoreboard (C2, W7) [P3]

| ID | Requirement |
|---|---|
| FR-11.1 | Per covered ward and category: masters reported, open count, median open age (days), citizen-confirmed fixed count. |
| FR-11.2 | Only verifiable data: no officer names, no "department failed" verdicts. Wording is neutral. |
| FR-11.3 | Masters with < 2 reporters may be shown in counts only, never as pins with photos (avoid identifying a single reporter's home). |
| FR-11.4 (W7) | The demo map uses **seeded data**, with every view labelled **"Sample data"**. Seeded and real records are never mixed in one aggregate. `status_unknown` is shown as its own bucket, never counted as fixed. |

### FR-12 Hindi audio readout (C4) [P3]

| ID | Requirement |
|---|---|
| FR-12.1 | A "listen" control plays the status in Hindi: category, department, "day X of Y" (with the suggested label if applicable), and the next step. The **audio is generated on the server** and played in the page; no browser speech engine is relied on. |
| FR-12.2 | Readout never includes PII (the masking check runs on the text before audio is generated). |

### FR-13 Call mode, helper share, printable dossier (S6, W3) [FR-13.1–13.3: P1 · FR-13.4–13.5: P2]

| ID | Requirement |
|---|---|
| FR-13.1 [P1] | "Call" offers the routed department's helpline from the Contact directory: PWD 1908 / DJB 1916 / MCD 155305 or 311 (311 only if verified live, Q8). Out of coverage → list the general helplines. The dialler opens only on the user's tap (no auto-dialling). |
| FR-13.2 [P1] (TM Q-19) | **Hindi call script**, shown in large text: greeting, issue + category, landmark + area/ward, "N citizens reported", the cited rule in one plain sentence (or "no official rule found"), and a request for a complaint number. ≤ 60 words; it never includes the PII of others. An English version is available. **Sequence:** the script is **played aloud first** (server-generated audio, as FR-12.1; replayable), **then** a **"Call now"** button opens the dialler. The script **stays on screen** after the call so the user can read it; read-aloud is not expected to continue once the dialler is open. |
| FR-13.3 [P1] | After the call, prompt: "Did you get a complaint number?" → number input (numeric keypad, or voice) → `filed` with channel = phone. "No" → keep `dossier_ready` and offer a retry (and, from P2, helper share/print). |
| FR-13.4 [P2] | **Printable dossier:** a PDF (A4, Hindi + English) with the complaint, landmark/ward, department, citation, deadline (with label), report count, and a **QR code** linking to the masked online dossier (photo evidence + pin). For handing in at the ward office. |
| FR-13.5 [P2] | **Helper share:** share the dossier link (or the PDF) via the device share sheet to a family member/RWA. The link view is read-only, masked, and lets the helper mark "Filed on their behalf" + ref no. (recorded against the original report). The link can be revoked by the reporter. |

**Acceptance [P1]**
- Given a PWD-routed pothole, when the user taps Call, then the Hindi script plays and is shown; then "Call now" opens the dialler prefilled with 1908.
- Given the user returns from the dialler, then the script is still on screen.
- Given the script, then it contains the landmark, category and rule sentence and has ≤ 60 words, with 0 PII matches.
- Given the user enters a ref no. after the call, then state = filed, channel = phone, and the master clock starts (if this is its first filing).

**Acceptance [P2]**
- Given the printed PDF, when its QR is scanned, then the masked dossier with photo opens; a revoked link shows "link no longer active".
- Given a helper marks "Filed on their behalf", then the reporter's tracker shows filed with the ref no.

### FR-14 Check-in nudges & passer-by confirm (S7, W4) [P2]

| ID | Requirement |
|---|---|
| FR-14.1 (Streamlit) | **Pending check-in cards** (no web push) for **all reporters** of a filed master, due on **day 3, day 7, and the deadline day** (counted from the master clock): "Did anyone come?" → one-tap **Yes, working on it / No / Fixed**. Due cards are shown **on login and on the home screen** until answered by any reporter or superseded by the next check-in point. |
| FR-14.2 | **Any reporter** can answer, and the answer updates the master for everyone (logged as a CheckIn). "Fixed" → opens the FR-9 after-photo flow (it is not resolved by the tap alone). "No" on the deadline day → `overdue` and the L1 offer. "Yes" → a note on the tracker; the clock is unchanged. |
| FR-14.3 | Reminder emails (FR-14.7): max 1 per master per check-in point; max 3 per user per day across issues (incl. FR-5.7 filing nudges and FR-9.4 vote reminders); none sent 22:00–07:00. Cards have no limit. |
| FR-14.7 (Streamlit) | **Optional reminder email to the citizen:** opt-in (asked after the first dossier, not at onboarding), sent to the user's own sign-in email only, with a link back to the pending card and no PII of others. In demo/test environments it goes only to the sandbox inbox (NFR-DEMO-1). This is the only email the system sends, and it never goes to a government address (BR-9). |
| FR-14.4 | **Passer-by:** a logged-in user whose current location is ≤ 50 m from an open master (and who isn't a reporter) may see "Is this still here? Yes / No / Not sure" (max once per user per master per 7 days). Answers are context only: "Yes" refreshes `last_seen_at`; ≥ 2 "No" answers prompt reporters to do the after-photo check. They never resolve or vote. |
| FR-14.5 | **Status unknown:** a master with no reporter check-in, vote or passer-by answer for 14 days after the deadline, or one that hits FR-9.7, → `status_unknown`. It is shown as "Status unknown, last seen DD-MM" and is **never auto-resolved**. Any later check-in, after-photo or passer-by "Yes" moves it back to the relevant tracking state. |
| FR-14.6 | Passer-by prompts do not reveal reporter identities or the report count's members. |

**Acceptance [P2]**
- Given a master filed on day 0, then check-in cards are due on day 3, 7 and Y, appear on login and home, and each offers the 3 one-tap answers.
- Given email reminders are opted in, then one sandbox email per check-in point is sent (none 22:00–07:00, max 3/day); with no opt-in, no email is sent.
- Given reporter B taps "No" on the deadline day for a master filed by A, then the master → overdue for both.
- Given a reporter taps "Fixed", then the after-photo flow opens and the state is unchanged until a valid photo.
- Given a non-reporter 40 m from an open master, then the "still here?" prompt shows at most once in 7 days; their answer changes no state.
- Given no activity for 14 days after the deadline, then status_unknown; a later "Yes" check-in restores the prior state.

### FR-15 Blind, end-to-end evaluation (M7, W1) [P1]

| ID | Requirement |
|---|---|
| FR-15.1 | **Blind labels, two passes:** pass 1, a **second person who has not seen the RoutingRules table** labels the expected department, category and SLA from **official/independent sources** (PWD notified road list, NGT/charter docs, news reports of which agency fixed what), recorded in `label_source`. These labels are **frozen** (timestamp + hash) **before** pass 2, which fills in rule IDs and gold chunk IDs. Pass 2 never changes a pass-1 label. |
| FR-15.2 | The curator who builds the rules must not label the test set. The labeller must not edit the rules. The rules dataset is frozen (version recorded) before the eval run. |
| FR-15.3 | **End-to-end input:** each case runs from a raw photo + voice/text + pin (or landmark), through the full pipeline. No hand-fed category or ward. |
| FR-15.4 | **Per-stage metrics** in the eval report: intake (category accuracy, junk precision, ask-rate, drain_type accuracy), location (ward accuracy, landmark geocode within 100 m), road (snap segment correct, row_class correct, one-tap question rate), routing (dept accuracy), citation (Ragas + correctness), plus end-to-end dept accuracy. Each failure is attributed to the first stage that went wrong. |
| FR-15.5 | Scripted answers for clarification prompts are stored per case so runs are reproducible. |
| FR-15.6 | **Two sets:** a **test set of 100** cases, frozen and **hashed** (hash recorded in every eval report), and a separate **dev set of 50** for all tuning (thresholds, confidence factors, prompts). Nothing is tuned on the test set; a changed test-set hash invalidates comparisons. |

**Acceptance [P1]**
- Given the eval report, then every case has a `label_source` that is not the rules table, and the labeller ≠ the rules curator.
- Given the eval run, then the per-stage table and the end-to-end routing accuracy are both reported, with the rules dataset version and the test-set hash.
- Given the label file, then every pass-1 label's freeze timestamp precedes the pass-2 rule/chunk fill.

---

## 4. Issue Lifecycle

States apply to the **MasterIssue**. The per-report state is the same until a report joins a master, after which it mirrors the master. Per-user filing/escalation steps are tracked on the Report, because each citizen files themselves. **Deadlines and overdue/status_unknown are computed from the master clock** (`clock_started_at` = first filed date, reset on re-route; TM Q-10, Q-11).

```
draft → needs_clarification ⇄ draft
draft → rejected_junk | out_of_scope | out_of_coverage   (terminal for routing)
draft → routed → dossier_ready → filed → overdue → escalated_L1 → escalated_L2 → escalated_L3
any of {filed, overdue, escalated_*} → bounced → routed (re-route, clock restarts) | dossier_ready (evidence letter)
any of {filed, overdue, escalated_*, bounced} → resolved_pending_vote → resolved | reopened | status_unknown
resolved (≤ 30 days) → reopened   (a new report taps "Reopen?", FR-6.6)
any of {filed, overdue, escalated_*} → status_unknown   (no activity for 14 days after the deadline)
status_unknown → prior tracking state | resolved_pending_vote   (any new check-in / after-photo)
reopened → (the state before resolved_pending_vote; clock continues)
```
`status_unknown` is never terminal and never counts as resolved.

| From | To | Trigger |
|---|---|---|
| — | draft | User submits input (FR-1.1) |
| draft | needs_clarification | Intake confidence < 0.7, C3 mismatch, drain type unresolved, or road/zone class unresolved → issue-matched one-tap question / two candidates (FR-3.7) |
| needs_clarification | draft | User answers |
| draft | rejected_junk | Only input is a junk image, and the user doesn't add a valid photo/text |
| draft | out_of_scope | Category outside the 4 |
| draft | out_of_coverage | Ward not curated (FR-3.2) |
| draft | routed | Confirmed pin + category ≥ 0.7 + department chosen. Dedup runs first; if joined, the master's state applies |
| routed | dossier_ready | Dossier generated (FR-5); day-1/day-3 "Did you file it?" nudges start (FR-5.7) |
| dossier_ready | filed | User marks "I filed it" (FR-5.4), enters a ref no. after a call (FR-13.3), or a helper marks "filed on their behalf" (FR-13.5). First filing on the master sets `clock_started_at` |
| filed | overdue | Day Y+1 with no resolution (FR-7.3), or a reporter answers "No" at the deadline check-in (FR-14.2) |
| overdue | escalated_L1 | User marks L1 appeal filed |
| escalated_L1 | escalated_L2 | L1 wait elapsed + user marks the CPGRAMS draft filed |
| escalated_L2 | escalated_L3 | L2 wait elapsed + user marks the RTI filed |
| filed/overdue/escalated_* | bounced | User taps "Dept rejected" (FR-3.11) |
| bounced | routed | User accepts a re-route: new dept, **clock restarts**, old steps kept in history (FR-3.11) |
| bounced | dossier_ready | Evidence letter generated against the same dept |
| filed…bounced | resolved_pending_vote | A valid after-photo was submitted (FR-9.2); R and voter set frozen |
| resolved_pending_vote | resolved | Vote rule passes (FR-9.5) |
| resolved_pending_vote | reopened | Vote rule fails / tie |
| resolved_pending_vote | status_unknown | Day 14, still undecidable (FR-9.7) |
| resolved | reopened | Within 30 days of resolution, a new same-category report ≤ 50 m taps "Reopen?" (FR-6.6) |
| filed/overdue/escalated_* | status_unknown | No reporter check-in/vote/passer-by answer for 14 days after the deadline (FR-14.5) |
| status_unknown | prior state / resolved_pending_vote | Any check-in or passer-by "Yes" → prior state; a valid after-photo → resolved_pending_vote |
| reopened | prior state | Immediately; reminders resume |

Unfinished server-side drafts expire after 7 days (they are deleted along with their media). Session drafts (FR-1.11) end with the session.

---

## 5. Data Entities (conceptual)

`🔒` = encrypted at rest. `*` = required.

**User:** id*, auth_provider* (email_otp / google), email🔒* (+ lookup hash), name🔒, phone🔒 (optional; only needed for opt-in filings), preferred_language* (hi/en), privacy_accepted_at*, created_at, deleted_at, rate_limit_counters (keyed on user id).

**Report:** id*, user_id*, master_issue_id, created_at*, inputs {photo_ids[] (≤ 3), voice_id, text}, transcript, transcript_lang, category*, severity, intake_confidence*, drain_type (sewer_manhole / open_nala / unknown) + confidence, junk_flag + reason, photo_quality (ok/low), location_verified (bool), capture_gps {lat, lng, accuracy_m, ts}, pin_seed_fix {lat, lng, accuracy_m, ts}, pin* {lat, lng}, pin_source*, landmark_text, state*, dossier_ready_at, filing_nudges_sent [day1, day3], filed {channel (portal/app/email/phone/in_person/helper), ref_no, filed_at, filed_by (self/helper)}, email_reminder_opt_in (bool), escalation_steps[] (EscalationStep ids), opt_in_identity_on_filing (bool).

**SessionDraft (Streamlit session state only, TM Q-9):** draft inputs, captured fixes, answers so far, updated_at. Lives only for the session; not persisted until the user submits.

**MasterIssue:** id*, category*, centroid {lat, lng}*, ward_id, representative_photo_id, reporter_user_ids (distinct)*, reporter_count (derived), department_id, routing_rule_id, routing_confidence, alternatives [{dept_id, confidence, reason}], clock_started_at (first filed date; reset on re-route), deadline {sla_days, due_date (derived from clock_started_at), cited (bool), citation_id, label (official / suggested)}, routing_history [{dept_id, rule_id, clock_started_at, ended_at, reason}], vote_round {after_photo_at, frozen_voter_ids[], frozen_r}, state*, last_seen_at, last_activity_at, previous_master_id, created_at, resolved_at, is_sample (bool, W7 seeded demo data).

**Ward:** id*, ward_no*, name_en*, name_hi, zone (MCD zone), boundary_polygon*, source + version (e.g. "SEC Delhi Delimitation 2022"), in_coverage (bool)*.

**RoadSegment (W2):** id*, osm_way_id*, name, osm_highway_class, geometry*, ward_ids[], pwd_listed (bool)*, pwd_list_ref (citation to the notified list entry), row_class (≥60ft / <60ft / unknown)*, row_source (pwd_list / curated / inferred), owner_dept_id, last_verified*, confidence*.

**SpecialZone (W2):** id*, type* (yamuna_floodplain / ghat / ndmc_area / cantonment / dda_land), name, polygon*, source_citation_id*, last_verified*.

**RoutingDecision (per report, W2):** snapped_segment_id, snap_distance_m, zone_id, image_road_judgement (wide_multilane / narrow_gali / unclear + confidence), question_type (road / drain / ghat / none), user_answer (main_road / inner_lane / open_nala / sewer_manhole / river_bank / in_water / not_sure / none), evidence_used, rule_id, confidence_factors {rule, evidence, citation, staleness}, confidence, factor_config_version.

**CheckIn (S7):** id*, master_issue_id*, user_id*, kind* (reporter_nudge / filing_nudge / passer_by), checkpoint (day1 / day3 / day7 / deadline / adhoc), answer* (yes_working / no / fixed / still_here / gone / not_sure / filed / not_yet / help), distance_m (passer-by), created_at*.

**ShareLink (S6):** id*, report_id*, token* (unguessable), created_at, revoked_at, helper_filed {ref_no, filed_at}.

**Department:** id*, code* (MCD, PWD, DJB, NDMC, DDA, I&FC, DCB, …), name_en/hi*, contacts[] → Contact {channel_type (helpline/app/portal/email/X), value, deep_link, scope (ward/zone/citywide), nodal_officer_designation (designation only, not a person's name), last_verified*, source_url*}.

**RoutingRule:** id*, priority*, conditions {ward_ids | "any", category, row_class (≥60ft / <60ft / unknown / n/a), special_zone (yamuna_floodplain, ghat, ndmc_area, cantonment, dda_land, none), asset (road / storm_drain / sewer / streetlight / open_plot / river_bank / water)}, department_id*, sla_days, sla_citation_id, source_citation_ids[]*, curated_by*, last_verified*, confidence* (0–1), status (active / needs_review / retired), notes.

**SuggestedWaitConfig (FR-4.7):** default_sla_days (15), l1_wait_days (15), l2_wait_days (30), version.

**PolicyDoc:** id*, title*, issuer*, doc_type (charter/notification/guideline/act/circular/procedure), language, published_date, url*, retrieved_at*, ocr (bool), manually_checked (bool). **Chunk:** id*, doc_id*, section_ref*, page, text*, lang.

**Citation:** id*, chunk_id*, quoted_span*, claim_text*, used_in (dossier / letter / tracker / call_script).

**Dossier:** id*, master_issue_id*, report_id*, version*, complaint_hi*, complaint_en*, email_draft {to, subject, body}, deep_link, citations[]*, deadline snapshot, reporter_count snapshot, generated_at*, generation_ms*.

**EscalationStep:** id*, report_id*, department_id* (the step belongs to one department's clock), level (L0 reminder / L1 / L2 / L3 / evidence_letter), draft_text_hi/en, target (designation/channel), citation_ids[], unlocked_at, filed_at, ref_no, due_at, superseded_by_reroute (bool).

**OutcomeFeedback (C1):** id*, report_id*, master_issue_id*, user_id*, routing_rule_id*, outcome*, bounced_to_dept_id, note, created_at.

**ResolutionVote:** id*, master_issue_id*, user_id* (must be in vote_round.frozen_voter_ids), after_photo_id (submitter only), after_gps {lat, lng, accuracy_m}, distance_m, effective_distance_m (distance − accuracy), ai_score (looks_fixed/not/unclear, advisory only), vote (fixed/not_fixed), created_at. Unique (master_issue_id, user_id, vote_round).

**EvalCase:** see §11 schema; plus set (test / dev), label_pass1_frozen_at, label_pass1_hash.

---

## 6. Escalation Ladder Rules

| Level | Unlocks | Draft content | Wait before next | Wait source |
|---|---|---|---|---|
| L0 Reminder | Day Y+1 (overdue) | In-app nudge + "re-share your complaint ref" text | — | — |
| L1 First appeal | With L0 | Letter to the dept **nodal/PG officer** (designation from Contact): original ref, date, cited SLA, days overdue, photo, N reporters | Cited appeal SLA, else **suggested 15 days** (FR-4.7) | Dept charter/PGMS doc ⟨to curate⟩ |
| L2 CPGRAMS draft | L1 filed + wait elapsed | Text pre-shaped for the pgportal.gov.in fields (+ deep link); includes L1 ref | **CPGRAMS redressal norm** (legal/administrative anchor, W6), else **suggested 30 days** (FR-4.7) | CPGRAMS procedure/DARPG guidelines ⟨to verify the current published figure⟩ |
| L3 RTI draft | L2 filed + wait elapsed | RTI application to the dept PIO: action taken on ref X, officer designation responsible, dates, reasons for delay (questions, not accusations) | **30 days** reply (statutory) | RTI Act 2005 s.7(1) |
| Evidence letter (S5) | On "Dept rejected", offered first if the rule says it is that dept's | Ownership rule citation, pin, ward, RoW class, request to act or forward per rule | Resets to that dept's SLA | Routing rule citations |

Rules: (1) Every number shown is either cited or labelled **"Suggested deadline (no official rule found)"** / "कोई आधिकारिक नियम नहीं मिला" (W6, TM Q-4). (2) Suggested values live in one config (FR-4.7: 15/15/30) and are never presented as official. The RTI 30 days and the CPGRAMS norm are the legal anchors. (3) Drafts are bilingual and masked, and require the user to tap "I filed it". (4) The RTI draft states fee/payment as a citizen action; the app does not process payment. (5) No step names an officer personally; designations only. (6) A re-route restarts the ladder for the new department; the old department's steps stay in history (TM Q-11).

---

## 7. Curated Datasets Spec

### 7.1 Routing rules: decision order
Evaluate active rules for the pin's ward by ascending `priority`; the first match wins. Unmatched → `no rule` → state "no cited rule found" + alternatives.

1. **Special zone overlays** (priority 10): pin inside a curated polygon (Yamuna floodplain/ghat, NDMC area, Cantonment, DDA park/land). E.g. ghat + garbage → the agency per the curated rule (MCD vs DDA vs I&FC is **to verify** per ghat; NGT immersion guidelines as citation). The ghat question (FR-3.7: river bank vs in the water) sets the asset.
2. **Road RoW class** (priority 20): pin snapped to an OSM road segment within 25 m (FR-3.4), with the RoW class from the PWD notified road list, else the image tie-break, else the user's one-tap answer (FR-3.5–3.7). Pothole/streetlight on ≥60 ft → PWD; <60 ft / inner lane → MCD. Cite the 2012 transfer notification/PWD circular [s1] and the list entry.
3. **Asset type by category** (priority 30): sewage overflow / `drain_type = sewer_manhole` → DJB; garbage → MCD; storm-drain waterlogging / `drain_type = open_nala` → owner of the drain (PWD for ≥60 ft road drains, MCD for colony drains, I&FC for major drains: **to verify**).
4. **Ward default** (priority 90): the ward's civic body for the category.

**Road data (W2):** import the OSM road segments for the covered wards (+ a 200 m buffer) as RoadSegment rows. Match each against the **PWD notified road list** (to collect: the list of roads maintained by PWD Delhi, by name/stretch) and set `pwd_listed` + `pwd_list_ref`. Segment confidence reflects the evidence: on the PWD list = 0.9; curator-verified on the ground = 0.85; unlisted (left to the tie-break) = n/a. Unlisted ≠ MCD by default.

**Special-zone polygons:** Yamuna floodplain + ghats (incl. Kalindi Kunj), NDMC boundary, Delhi Cantonment, DDA land/parks in the covered wards. Each polygon needs a source citation.

**Example rule rows (illustrative, to verify; confidences are recalibrated on the dev set, FR-3.8):**

| priority | ward | category | row_class | special_zone | dept | confidence |
|---|---|---|---|---|---|---|
| 10 | any covered | garbage_dump | n/a | ghat | ⟨verify: MCD/DDA/I&FC⟩ | 0.6 |
| 20 | any covered | pothole | ≥60ft | none | PWD | 0.9 |
| 20 | any covered | pothole | <60ft | none | MCD | 0.85 |
| 30 | any covered | waterlogging_sewage (sewer) | any | none | DJB | 0.85 |
| 30 | any covered | waterlogging_sewage (open nala) | any | none | ⟨verify: PWD/MCD/I&FC by drain⟩ | ⟨dev set⟩ |

### 7.2 Contact directory
One Contact row per (department, channel, scope). Seed: MCD 311 app / 155305, PWD 1908, DJB 1916, CPGRAMS, published grievance emails, X handles, nodal/PG officer designations. Every row needs `source_url` + `last_verified`; anything > 90 days unverified shows "may be outdated". The unified 311 helpline goes in only if verified live.

### 7.3 Policy corpus (to collect, ~12–15 docs)
| Doc type | Purpose |
|---|---|
| MCD citizen charter (sanitation, roads, streetlights) | SLA days, ownership |
| PWD Delhi citizen charter + Feb 2025 circular re 60 ft roads [s1] | Ownership, SLA |
| 2012 notification transferring ≥60 ft RoW roads to PWD | Ownership (primary citation) |
| PWD notified/maintained road list (W2) | Segment-level ownership; also a blind-label source (W1) |
| DJB citizen charter (sewer overflow, blockage) | Ownership, SLA |
| NDMC charter (if any covered ward borders NDMC) | Ownership |
| NGT orders/CPCB guidelines on idol immersion | Ghat garbage duties |
| Yamuna floodplain jurisdiction docs (DDA/I&FC) | Ghat/floodplain overlay |
| MCD PGMS/grievance escalation procedure | L1 target + timeline |
| CPGRAMS procedure / timeline | L2 |
| RTI Act 2005 (s.6, s.7, s.19) + Delhi RTI rules (fees) | L3 |
| Solid Waste Management Rules 2016 / MCD SWM bye-laws | Garbage obligations |

Each doc: OCR if scanned, with key sections manually checked (`manually_checked = true` is required for any doc cited as an ownership rule).

### 7.4 Ward selection (8 first, growing to 20)
Use **MCD 2022 delimitation** numbering (250 wards; source: Delhi SEC ward maps). **Coverage starts at 8 wards** (TM §5 decision 3): the Kalindi Kunj ghat area, wards with PWD roads through MCD colonies, and at least one boundary edge (ward edge / NDMC or Cantonment border). It then grows toward **20** using the criteria: (a) ~4 wards around the Yamuna/Kalindi Kunj ghat area; (b) ~8 wards containing known ≥60 ft PWD roads cutting through MCD colonies (e.g. Mathura Rd, Outer Ring Rd, Ring Rd, Mehrauli–Badarpur Rd); (c) ~4 wards with heavy monsoon waterlogging + DJB sewer complaints; (d) ~2 wards bordering NDMC or Cantonment for overlay tests; (e) a mix of MCD zones; (f) the eval sets (§11) must be satisfiable from the covered wards. The eval runs on whatever wards are `in_coverage` at freeze time, recorded with the dataset version.

Candidates:

| Ward | Why | Status |
|---|---|---|
| 185 Madanpur Khadar East | Kalindi Kunj/Yamuna side | number verified (news + SEC map label) |
| 186 Madanpur Khadar West | Kalindi Kunj side | number verified (news) |
| 187 Sarita Vihar | Mathura Rd / Kalindi Kunj Rd (PWD) vs colony lanes | number verified (news) |
| 180 Badarpur | Mathura Rd / MB Rd boundary | SEC ward map exists |
| Jaitpur, Molarband, Tughlakabad Extn | Narrow MCD lanes beside MB Rd, waterlogging | to verify numbers |
| Okhla / Abul Fazal / Zakir Nagar area wards | Yamuna-side, sewer overflow | to verify |
| Kalkaji, Govindpuri, Lajpat Nagar, Defence Colony area wards | Outer Ring Rd/Ring Rd vs colony lanes | to verify |
| Mayur Vihar / Trilokpuri area wards (east bank) | Yamuna ghats (east), NH-24 | to verify |
| Sangam Vihar, Madangir | Dense lanes, garbage, waterlogging | to verify |
| One ward bordering NDMC (e.g. near Lodhi Rd) | Overlay test | to verify |

**Kalindi Kunj ghat:** the exact ward containing the ghat is **not verified**; confirm it on the SEC 2022 map (likely 185/186/187). Final list of the first 8 and the growth order: curator, before dataset build (Q10).

---

## 8. Business Rules

| ID | Rule |
|---|---|
| BR-1 | Intake or routing confidence < 0.7 → ask the user (one issue-matched question, FR-3.7). Never auto-decide. |
| BR-2 | Dedup = same category ∧ ≤ 50 m ∧ (open, or resolved ≤ 30 days → "Reopen?") ∧ image similarity ≥ 0.85; no photo or low-quality photo → location + category candidates. The user always confirms the join. |
| BR-3 | Reporter count = distinct user_ids. The same user is counted once per master. |
| BR-4 | Rate limits (keyed on user id): ≤ 10 reports/user/day; ≤ 1 new report per user per category within 50 m per 24 h (a second one is redirected to the existing one); ≤ 3 junk rejections in 10 min → 15 min cooldown. ⟨values tunable⟩ |
| BR-5 | Resolution: in-app after-photo with distance − accuracy ≤ 30 m, else rejected. R and the voter set freeze at upload. R = 1 → the photo resolves; R = 2 → both must vote fixed; R ≥ 3 → strict majority, ≥ 2 fixed. **The AI is advisory only and never counts as a vote.** Decided at day 7, or later when decidable; still undecided at day 14 → status_unknown. |
| BR-6 | Out of coverage → no routing claim, only general channels. |
| BR-7 | The road decides, not the ward: zone → PWD list → image tie-break → one-tap issue-matched question → user picks between two candidates. Boundary spots take this normal path. |
| BR-8 | No citation → "no cited rule found"; deadline = "Suggested deadline (no official rule found)" / "कोई आधिकारिक नियम नहीं मिला", always shown; confidence lowered. |
| BR-9 | Nothing is ever transmitted to a government system by the app. The only outbound email is the citizen's own opt-in reminder (FR-14.7). |
| BR-10 | Gallery photos are allowed for intake (flagged) but not for resolution. |
| BR-11 | Officers are referenced by designation only, in every output. |
| BR-12 | Hindi + English are both always generated for complaint/letters. UI text follows preferred_language. |
| BR-13 | Unknown is never resolved: no state transitions to `resolved` without the FR-9.5 rule passing. |
| BR-14 | Build order is strict: P1 → P2 → P3 (W7). The demo map uses seeded data labelled "Sample data". |
| BR-15 | One master clock: it starts at the master's first filed date; later reporters inherit it; a re-route restarts it for the new department (TM Q-10, Q-11). |
| BR-16 | Online-only v1: no queued or background submission; drafts are kept in the session and resumed manually (TM Q-9); not installable, no offline mode. |
| BR-17 | All thresholds and confidence factors are tuned on the dev set only, never the test set (FR-15.6). |

---

## 9. Edge Cases (beyond PRD §8)

| Case | Expected |
|---|---|
| Voice note in a language other than HI/EN (e.g. Punjabi) | Low confidence → ask the user to choose a category or type it |
| Silent/noise-only voice note | "Couldn't hear, please retry or type" |
| Phone number spoken as words or in Devanagari digits | Normalised, then masked (FR-8.6) |
| Pin dragged > 500 m from the seed fix | Allowed, but `location_verified = false` |
| Camera opened, best fix was before the user walked away | Pin seeds from the best-accuracy fix; the user drags/confirms (FR-2.1) |
| Master re-routed after bounce while others are already filed | All reporters notified of the new dept + reason; the master clock restarts for the new dept; old deadlines and steps move to history |
| Reporter deletes account mid-vote | Their vote is removed (see Q17 for the effect on frozen R) |
| Reporter joins while a vote is running | Not in the frozen voter set; can see but not vote (FR-9.4) |
| After-photo at 35 m with 3 m accuracy | 32 > 30 → rejected, "Move closer" |
| Two masters within 50 m of the same category (pre-existing) | Show both, nearest first; curator can merge (out of v1 UI, manual) |
| New report near a master resolved 20 days ago | "Reopen?" offered (FR-6.6) |
| Dark/blurry photo at dedup | Location + category candidate list, user confirms (FR-6.7) |
| Rule's `last_verified` > 12 months | Routing still shown, staleness factor (starting ×0.9), "data may be outdated" |
| Ward boundary data updated (re-delimitation) | Ward source/version recorded; old reports keep their original ward |
| Backend unreachable mid-draft | Draft kept in the session; "Couldn't reach the server, your draft is kept"; manual Retry; nothing auto-submits (FR-1.11) |
| Session ends (tab closed / session timeout) before submit | Session draft is lost (Q20) |
| Location component blocked or returns nothing | Landmark fallback (FR-2.4), or the user clicks the map to place the pin |
| Pin on a flyover/underpass above/below another road | Snap picks the nearest within 25 m; if two segments are within 15 m → one-tap question |
| PWD-listed road name matches but geometry doesn't (OSM misalignment) | Treat as `unknown` → tie-break; log for curator |
| Waterlogging where the photo shows neither a manhole nor a nala | `drain_type = unknown` → drain question |
| User never taps "I filed it" | Day-1 and day-3 "Did you file it?" nudges, then stays dossier_ready (FR-5.7) |
| User never opts in to reminder emails | Check-in cards on the next login/home visit only (FR-14.1) |
| Helper link forwarded widely | Masked view only; reporter can revoke (FR-13.5) |
| Phone call: helpline gives no complaint number | Stay dossier_ready; offer retry (P1) / helper share / print (P2) |
| Blur detector errors on a photo | Photo left out of shared outputs (FR-8.4) |

---

## 10. Non-Functional Requirements

| ID | Requirement | Check |
|---|---|---|
| NFR-PERF-1 | Intake submit → dossier_ready **< 60 s p90**, measured on the eval set end-to-end (excluding user think-time at clarification prompts). | Timed runs, report p50/p90 |
| NFR-PERF-2 | Pin → ward lookup < 1 s; dedup check < 3 s. | Timer logs |
| NFR-SEC-1 | PII fields encrypted at rest; keys stored outside the DB; a keyed hash column for email/phone lookup. | Dump inspection |
| NFR-SEC-2 | All traffic over TLS; media URLs not publicly guessable, time-limited. | Pen-test checklist |
| NFR-SEC-3 | Outbound masking on every generated artifact (FR-8.3, FR-8.6); regex scan in CI over generated samples, including Devanagari-digit and number-word cases. | Automated |
| NFR-SEC-4 | Curator/admin access to PII is audited (who, when, why). | Audit log |
| NFR-SEC-5 | **Auth:** sign-in by **email OTP or Google sign-in** only; **no phone OTP** (cost). OTPs are single-use, short-lived and rate-limited. | Auth tests |
| NFR-PRIV-1 | Retention: voice recordings deleted 30 days after the master resolves or 90 days after creation (whichever is first, ⟨confirm Q2⟩); photos kept while the master is open + 90 days; PII deleted ≤ 7 days after an account deletion request. | Scheduled job test |
| NFR-PRIV-2 | Data used only for the complaint; no third-party analytics receive PII. Hosted models that may train on prompts receive **test data only**, never real user data (TM §5 decision 2). | Review |
| NFR-I18N-1 | All user-facing strings in Hindi and English; Devanagari renders correctly; Hinglish input accepted. One canonical string per label (e.g. "कोई आधिकारिक नियम नहीं मिला"). | Visual + string coverage |
| NFR-A11Y-1 | WCAG 2.1 AA: contrast, tap targets ≥ 48 px, screen-reader labels, no information conveyed by colour alone. | Audit tool + manual |
| NFR-A11Y-2 | Low-literacy: every core action is reachable without typing (voice + icons); a P1 filing path exists without reading or typing (Call mode, FR-13.1–13.3); key status available as audio (C4). | Walkthrough with Ramesh persona |
| NFR-A11Y-3 | Elderly: text scalable to 200% without breaking; no time-limited interactions. | Manual |
| NFR-DEMO-1 | Demo/test environments route any email only to a **sandbox inbox**; real official addresses are displayed but sending to them is blocked by config. This covers the citizen reminder emails (FR-14.7). | CI test: gov domain → blocked |
| NFR-REL-1 | Generation failure → the user keeps their draft and can retry; no data loss. | Fault injection |
| NFR-REL-2 | A failed backend call never loses the session draft (FR-1.11). | Fault injection (backend down) |
| NFR-DEV-1 | Mobile web on low-end Android (2 GB RAM, 3G) usable; photos compressed to ≤ 1280 px on upload. iOS is best effort. Not installable; no service worker; no offline mode. | Device test |

---

## 11. Evaluation Spec (M7, W1) [P1]

Blind labelling, end-to-end runs and per-stage reporting follow FR-15.

**Sets (FR-15.6):** a **test set of 100 cases**, frozen and hashed, and a separate **dev set of 50 cases** for tuning. Never tune on the test set. Image-only sets (junk, dedup pairs, before/after) are split the same way, test vs dev.

**Coverage minimums (test set, TM §6):**

| Slice | Minimum |
|---|---|
| Should-ask cases | ≥ 30 |
| Boundary roads (service lanes beside listed roads, junctions, ward edges) | ≥ 20 |
| Sewer vs storm drain | ≥ 20 |
| Night / blurry photos | ≥ 20 |
| Per language (Hindi, English, Hinglish) | ≥ 15 each |
| Junk filter images | 50 junk + 50 valid |
| Dedup pairs (incl. 45–55 m hard negatives) | ≥ 60 |
| Before/after resolution pairs | ≥ 20 |
| Ghat/floodplain | ≥ 5 |
| Landmark-only | ≥ 10 |
| Out-of-coverage | ≥ 5 |
| Out-of-scope | ≥ 3 |

Cases can count toward several slices. Voice clips come from **real speakers** (not TTS, not the developer).

**Labelling (FR-15.1):** pass 1, the blind second labeller sets department, category, SLA and drain type from official sources, and those labels are frozen (timestamp + hash). Pass 2 then fills in rule IDs and gold chunk IDs. **Inter-labeller agreement (Cohen's kappa)** is measured on **≥ 20 cases** labelled independently by two people and reported; boundary cases get the two-person check, both blind to the rules.

**Label schema per case:**
`case_id, set (test/dev), inputs {photo, voice, text}, input_lang, speaker_id (real speaker), pin {lat,lng} | landmark_text, expected_ward_no, expected_segment_osm_id, expected_row_class, expected_zone, expected_category, expected_drain_type | null, expected_severity, expected_should_ask (bool), expected_question_type (road/drain/ghat/none), expected_junk (bool), expected_dept (or "out_of_coverage"/"out_of_scope"), acceptable_alt_depts[], expected_rule_id (pass 2), gold_citation {doc_id, section_ref} (pass 2), gold_context_chunk_ids[] (pass 2), reference_answer (dept + why + SLA), expected_sla_days | null, dup_of_case_id | null, tags[] (night, hinglish, boundary, ghat, sewer, storm_drain…), scripted_answers {prompt → answer}, label_source {type: pwd_list / charter / ngt / news_fix_report / field_visit, ref/url}, labeller (≠ rules curator), label_date, pass1_frozen_at, pass1_hash`.

Rules: labels for dept/SLA come from the `label_source`, **never from the rules table** (W1).

**Per-stage report (FR-15.4):** intake → location → road → routing → citation → end-to-end, each with its own accuracy plus a "first failing stage" breakdown.

**Metrics & targets (PRD §7):**

| Metric | Definition | Target |
|---|---|---|
| Routing accuracy (end-to-end) | predicted dept == expected_dept from raw inputs; asked-then-answered cases scored after the scripted answer | ≥ 85%, reported with its 95% interval (at n = 100, 85% is ±~7 points) |
| Road stage | row_class correct / cases on roads; one-tap question rate (report only) | report only |
| Drain type | drain_type correct / sewer-vs-storm cases | report only |
| Faithfulness (Ragas) | dossier rule/deadline statements vs the retrieved contexts | ≥ 0.85 (mean of 3 runs) |
| Answer correctness | dossier dept/rule/deadline vs reference_answer and gold_citation (faithful-but-wrong must fail) | report alongside faithfulness ⟨target: Q18⟩ |
| Context precision / recall (Ragas) | retrieved chunks vs gold_context_chunk_ids / reference_answer | ≥ 0.80 each (mean of 3 runs) |
| Junk filter precision | rejected ∧ expected_junk / rejected | ≥ 90% (also report recall) |
| Dedup precision | merged ∧ true dup / merged (incl. hard negatives) | ≥ 90% (also report recall) |
| Intake → dossier | p90 seconds | < 60 s |
| Uncited claims | count from the FR-4.4 checker over all dossiers | 0 |
| Ask-when-unsure | asked / cases with confidence < 0.7 (and expected_should_ask) | 100% |
| Question matches issue | asked question_type == expected_question_type | report only |
| Label agreement | Cohen's kappa on ≥ 20 double-labelled cases | report |
| Category accuracy | informational | report only |

**Ragas application:** for each case, `question` = the routing query (category + ward + road/zone context), `contexts` = retrieved chunks used for citations, `answer` = the dossier's dept/rule/deadline sentences, `ground_truth` = reference_answer. Run on all in-coverage cases, **3 times**; report mean and variance per metric, plus per-category and boundary-tag slices. Ragas runs manually or weekly, never on every push, with cached model responses. Eval report = one table + failure list with case_ids, stored with the eval date, rules dataset version, test-set hash and kappa.

---

## 12. Traceability

| PRD | Phase | FR / NFR |
|---|---|---|
| M1 | P1 | FR-1.1–1.6, FR-1.10, FR-1.11, BR-1, BR-16 |
| M2 | P1 | FR-2.1–2.5 |
| M3 | P1 | FR-3.1–3.10, BR-6, BR-7, §7.1, §7.2, §7.4 |
| M4 | P1 | FR-4.1–4.7, BR-8, §7.3 |
| M5 | P1 | FR-5.1–5.4, 5.6, 5.7, BR-9, BR-15, NFR-DEMO-1 |
| M6 | P1 | FR-6.1–6.7, BR-2, BR-3 |
| M7 | P1 | FR-15.1–15.6, §11, BR-17 |
| M8 | P1 | FR-8.1–8.6, NFR-SEC-*, NFR-PRIV-* |
| S1 | P2 | FR-1.7, BR-10 |
| S2 | P2 | FR-1.8 |
| S3 | P2 | FR-9.1–9.8, BR-5, BR-13 |
| S4 | P2 | FR-7.1–7.7, §6 |
| S5 | P2 | FR-3.11, §6 evidence letter |
| S6 | **P1** (call) / P2 (print, share) | FR-13.1–13.3 (P1), FR-13.4–13.5 (P2), NFR-A11Y-2 |
| S7 | P2 | FR-14.1–14.6, BR-13 |
| C1 | P3 | FR-10.1–10.3 |
| C2 | P3 | FR-11.1–11.4 |
| C3 | P3 | FR-1.9 |
| C4 | P3 | FR-12.1–12.2, NFR-A11Y-2 |
| C5 | P3 | FR-5.5 |
| W1 | P1 | FR-15, §11 label schema |
| W2 | P1 | FR-3.3–3.9, RoadSegment/SpecialZone, §7.1, BR-7 |
| W3 | P1/P2 | FR-13 (S6) |
| W4 | P2 | FR-14 (S7), `status_unknown` in §4 |
| W5 | P2 | FR-9.4, 9.5, 9.7, BR-5 |
| W6 | P1/P2 | FR-4.6, FR-4.7 (P1), FR-7.2, FR-7.6, §6, BR-8 |
| W7 | all | Phase tags, BR-14, FR-11.4 |

**TECH-MEETING-01 resolutions → where applied**

| TM | Applied in |
|---|---|
| §3 values | Header, FR-1.1, FR-1.8, FR-2.3, FR-3.4, FR-3.6, FR-3.8, FR-6.1, FR-6.6, FR-8.4, FR-9.2 |
| Q-1, Q-2 | FR-9.3–9.7, BR-5, §4, MasterIssue.vote_round |
| Q-3 | FR-9.2, BR-5, §9 |
| Q-4 | FR-4.6, BR-8, §6 rule 1, NFR-I18N-1 |
| Q-5 | FR-7.4 (L1 with L0, as §6); UX.md to update |
| Q-6 | FR-3.7, FR-3.8, RoutingDecision |
| Q-7 | FR-1.10, §7.1 step 3, Report.drain_type, §11 |
| Q-8 | FR-6.1, FR-6.6, §4 (resolved → reopened) |
| Q-9 | FR-1.11, BR-16, NFR-REL-2, SessionDraft, §9 |
| Streamlit change | Header, FR-1.11 (session draft), FR-2.1 (JS location + click-to-confirm pin), FR-5.7 + FR-14.1/14.3/14.7 (cards + citizen email, no push), FR-9.1 (reference photo), FR-12.1 + FR-13.2 (server audio), NFR-DEV-1, BR-9, BR-16, §9 |
| Q-10 | FR-5.4, FR-7.1, BR-15, MasterIssue.clock_started_at |
| Q-11 | FR-3.11, FR-7.7, §6 rule 6, §4, EscalationStep |
| Q-12 | FR-10.2 |
| Q-13 | FR-5.7, US-17, CheckIn |
| Q-14 | FR-6.7, §9 |
| Q-15 | Acceptance blocks split by phase; FR-4.7 (P1 config) |
| Q-16 | FR-13.1–13.3 → P1, US-13, NFR-A11Y-2 |
| Q-17 | FR-8.6, NFR-SEC-3 |
| Q-18 | REQUIREMENTS.md refs checked (FR-3.7 = issue-matched question / two candidates, FR-3.11 = rejected); stale refs are in UX.md (incl. §4.4) |
| Q-19 | FR-1 (no volume key), FR-5.2, FR-13.2 |
| Q-20 | FR-2.1, FR-2.5 |
| §5 decisions | Auth NFR-SEC-5; wards §7.4; Call mode P1; test-data-only hosted models NFR-PRIV-2 |
| §6 eval | FR-15.1, FR-15.6, §11 |

---

## 13. Open Questions

**Closed by TECH-MEETING-01 (2026-10-24):**

| # | Question | Resolution |
|---|---|---|
| Q1 | Sign-up identity | **Closed:** email OTP or Google sign-in; no phone OTP (NFR-SEC-5). Rate limits keyed on user id. |
| Q3 | Offline capture | **Closed:** online-only v1, draft kept in the session with manual retry (FR-1.11). Not installable. |
| Q13 | Web push on iOS | **Closed:** no web push at all (Streamlit); check-in cards + optional citizen email (FR-14.1, FR-14.7). |
| Q16 | Blur "fails closed" scope | **Closed (coordinator, 2026-10-24):** the photo is excluded only when blur processing errors; a photo with no detected faces/plates is shared (blurred where needed) (FR-8.4). |

**Still open:**

| # | Question | Default if unanswered |
|---|---|---|
| Q2 | Retention periods in NFR-PRIV-1 OK? | As written |
| Q4 | Ghat garbage owner at Kalindi Kunj: MCD, DDA or I&FC? Needs a source doc. | Rule confidence 0.6, show alternatives |
| Q5 | Suggested deadlines (15/15/30 days): acceptable? What is the current CPGRAMS redressal norm (source)? | As written, labelled "suggested" until verified |
| Q6 | For R ≥ 3, is the "minimum 2 fixed votes" floor OK? | As written |
| Q7 | Should the reporter's identity go into the dossier by default (portals need it), or should it be opt-in per filing? | Opt-in per filing |
| Q8 | Is the unified 311 helpline live? If so, should it be the primary channel? | Not used until verified |
| Q9 | C2 uses seeded "Sample data" (W7). Should real data ever be shown publicly in v1? | No; sample only |
| Q10 | Which 8 wards first, the growth order to 20, and who curates (§7.4)? | Curator, before dataset build |
| Q11 | Is the PWD notified road list obtainable in a usable form (PDF list by name/stretch)? If not, what is the fallback road evidence? | Image tie-break + one-tap question; segment confidence lowered |
| Q12 | Who is the second, blind labeller (W1)? The user will find one. | Must be named before pass-1 labelling starts |
| Q14 | Passer-by prompt radius 50 m and the once-per-7-days limit: acceptable? | As written |
| Q15 | S6: only Call mode (FR-13.1–13.3) was moved to P1. Do the printable dossier and helper share (FR-13.4–13.5) also move to P1? | Stay P2 |
| Q17 | A reporter deletes their account during a vote: does frozen R drop by one, or does their seat count as "not voted"? | R drops by one; vote removed |
| Q18 | Target for the answer-correctness metric (§11)? | Report only in the first run; set a target afterwards |
| Q19 | When a resolved master is reopened via "Reopen?" (FR-6.6), does the clock continue from the original filed date (likely already overdue) or restart? | Continue (as for vote-reopened) |
| Q20 | A session draft (FR-1.11) is lost when the session ends (tab closed, phone sleeps, server restart, free-host sleep). Is that acceptable, or should drafts be saved server-side for signed-in users? | Accept the loss in v1 |
| Q21 | Streamlit has no device share sheet: helper share (FR-13.5) becomes "copy link" + PDF download? | Copy link + PDF download |

---

**Handoff:** tech-lead → ARCHITECTURE.md using the TECH-MEETING-01 values above (no `⟨TBD-ARCH⟩` remains); calibrate the FR-3.8 factors on the dev set; plan the build P1 (incl. FR-13.1–13.3) → P2 → P3. qa-engineer → derive test cases from the phase-split Acceptance blocks, §4 transitions (incl. status_unknown, resolved → reopened, re-route clock restart), §8 BRs, §9 edge cases and §11 (incl. coverage minimums, kappa, 3× Ragas). ux-designer → apply Q-3, Q-4, Q-5, Q-9, Q-18 (incl. UX §4.4) and Q-19 in UX.md.

**Sources for ward numbers:** [Wikipedia: Madanpur Khadar](https://en.wikipedia.org/wiki/Madanpur_Khadar_Village) · [india.com 2022 MCD results](https://www.india.com/news/delhi/delhi-mcd-election-result-2022-full-list-winners-ward-wise-all-250-wards-winning-candidates-list-5784075/) · [SEC Delhi ward 180 Badarpur map](https://sec.delhi.gov.in/sites/default/files/SEC/universal-tab/ward_180_badarpur.pdf) · [SEC ward-wise population summary](https://sec.delhi.gov.in/sites/default/files/SCERT/circulars-orders/wardwise_population_summary.pdf)
