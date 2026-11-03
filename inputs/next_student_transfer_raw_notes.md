# NEXT Retail — Student Transfer Process & Automation Notes (Raw Input Brief)

## 1. Operational Context & Scale
- Every summer (and returning holiday windows), **1,000+ retail store staff who are university students** apply to transfer between their **Home Store** and a **University Town Store** across **~470 UK stores**.
- Today, a central HR / O&M team of **6–10 specialists (Lee's team, including Bryony)** coordinates this manually across **Google Forms**, **giant multi-tab Google Sheets**, automated mail-merges, **Yapster** chases, and **Store Manager (SM) cold calls**.

## 2. Current Manual Pain Points ("As-Is" Bottlenecks)
1. **Manual 30-Row Batch Copy-Pasting:** Specialists manually copy **30 rows at a time** from the Google Form response sheet into an `Area Converter` tab and `Stats Converter` tab, paste values into regional Area tabs, hide helper columns, and repair broken spreadsheet formulas when SMs accidentally edit cells.
2. **Batch 1 & Batch 2 Re-Sorting Churn:** Every week (`2–3x/week`), as new shifts appear or students decline offers, the team manually re-sorts tabs by Store and Star Rating.
3. **Cold-Calling & 24h Chases:** SMs cold-call matched students with 24-hour response windows or chase via Yapster; status updates in the master Sheet drift out of sync.
4. **Downstream Admin Exceptions (`NPS` & `NEP`):**
   - SMs must manually verify **3-day Contract Marketplace (`CMP`)** clearance (confirming existing store staff didn't claim the hours first) and ensure **Green Box protected shifts** are excluded.
   - Approved transfers require posting `'Do Not Post'` shifts in **NPS**, creating internal candidates in **NEP**, and raising manual HR support tickets whenever a contract falls under **`5.5 hours`** or has grade/step/QP pay discrepancies.

## 3. Target Matching Logic & Business Rules ("To-Be" Process)
1. **Eligibility Auto-Screen:**
   - **Eligible:** Permanent (`PERM`) staff rated **`3*` or `4*`** with an up-to-date review (and flagged *"Do NOT make leaver"* at their home store).
   - **Route Out (`INELIGIBLE_ROUTED`):** `1*` or `2*` ratings, ineligible `TEMP` contracts, or missing mandatory review sign-off.
2. **Store Priority Ordering (Demand Pressure):**
   - Process destination stores in descending order of **how many students selected that store as `Choice 1`** (so oversubscribed university cities like Manchester, Leeds, Nottingham, and Edinburgh allocate scarce shifts fairly first).
3. **Candidate Ranking Within Each Store:**
   - Rank eligible students by **`star_rating DESC` (`4*` before `3*`)**, then **`form_submitted_at ASC` (first-come, first-served)**, flagging whether the student has previously worked at their `Choice 1` store.
4. **3-Pass Shift Cascade (`Choice 1 -> Choice 2 -> Choice 3`):**
   - Match against **Scratchpad shifts** where `is_green_box_protected = false` AND `cmp_3day_cleared = true`.
   - **Unique-Availability Pass First:** If a shift window can only be covered by a single available candidate, allocate that fit before general contention.
   - **Single-Store Lock & `5.5hr` Contract Floor:** Combine multiple non-overlapping shifts **within the same store only** (never split a student across two stores). Once assigned hours reach **`>= 5.5 hours/week`**, mark the student **`MATCHED_COMPLETE`** so remaining shifts stay free for other students.
   - Cascade unmatched students from `Choice 1` to `Choice 2`, then `Choice 3`.
5. **Under-18 Safeguarding & Data Privacy:**
   - Some retail staff are `16–17` (`UNDER_18`) or have sensitive personal data. Raw names, personal mobile numbers (`07...`), emails, and DOBs should not sit in AI prompts or ad-hoc spreadsheets. Under-18s cannot work past `22:00` or exceed `8h/day` / `40h/week`.

## 4. Unresolved Policy Questions Flagged in Margin Comments (Bryony's Notes)
- **Edge Case 1 — Start-Date Gap (`[^c1]`):** What happens when a store's Scratchpad shift `available_from` date is *later* than the student's requested `target_transfer_date` (e.g., shift starts Oct 12, student arrives Sept 28)? Auto-match or flag for review?
- **Edge Case 2 — Home-Store Shift Release Timing (`[^c2]`):** At what exact trigger point do we un-match/release the student's existing shifts at their Home Store so they aren't double-rostered or left with zero pay?
- **Edge Case 3 — Sub-`5.5hr` Small Contracts (`[^c3]`):** If a store only has a single `4.0hr` shift left (`< 5.5hr` minimum contract), can a student opt into a `<5.5hr` small contract exception, or should we prompt the Store Manager to extend the shift by `+1.5hr`?

## 5. Project Plan Headline KPIs & Launch Expectations
- **80% reduction** in Store Manager & HR manual admin time.
- **90% reduction** in NEP pay-rate / grade-step / small-contract manual support cases.
- **100% elimination** of manual `Area Converter` / `Stats Converter` Google Sheet copy-pasting.
- Measurable YoY increase in **`3*` and `4*` permanent student talent retained** across UK stores.
- **Launch Posture:** Lee's HR team wants **100% human review (`Stage 1`)** at launch to build trust, before graduating clean `>=5.5hr` matches to exception-only review (`Stage 2`) and eventually bounded autonomous matching (`Stage 3`).
