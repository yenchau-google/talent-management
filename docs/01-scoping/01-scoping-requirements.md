# NEXT PLC — Student Transfer Assistant: Scoping & Requirements (`01-scoping-requirements.md`)

## 1. Executive Summary & Phase 1 MVP Boundary

Every summer and holiday window, **1,050+ permanent NEXT retail store colleagues who are university students** apply to transfer between their Home Store and a University Town Store across **470 UK stores**. Currently, the Central HR / O&M team (6–10 specialists) processes transfers manually via 30-row Google Form/Sheet copy-paste batches (`Area Converter` / `Stats Converter`), manual 2–3x/week store & star-rating re-sorts, Yapster chases, and Store Manager cold calls.

- **Phase 1 MVP Owner:** Central HR / O&M Specialists operating via the **Self-Service Intake Portal** and **Gemini Enterprise (`gemini-enterprise-retail-app`)**.
- **System Integration Boundary (Zero Shadow IT):**
  - **Upstream Intake:** Tokenized zero-PII Gold datasets (`students_gold` and `store_shifts_gold`) validated by the deterministic pre-flight gate (`scan_dataset.py`) and Dataplex Auto Data Quality (`next-student-transfer-dq-scan`).
  - **Downstream ERP Boundary (`NPS` / `NEP` / `Oracle`):** Phase 1 (`Stage 1: 100% HITL Review`) performs **zero direct writes** to downstream systems. Approved matches export strictly to staged **NEP `'Do Not Post'` CSV payloads** after human review.
  - **Enterprise Governance:** Deployed in `europe-west2` (`${GOOGLE_CLOUD_PROJECT}`) with a turn-key `IT_HANDOVER_BUNDLE/` for NEXT Enterprise IT (`roles/discoveryengine.admin`) promotion to `next-ge-poc`.

---

## 2. 3-Persona RACI & Phased Roadmap

| Persona | Role in Phase 1 MVP (`Stage 1: 100% HITL`) | Phase 2 / Phase 3 Evolution (`Stage 2` $\rightarrow$ `Stage 3`) | Data & Identity Boundary |
| :--- | :--- | :--- | :--- |
| **Central HR / O&M Specialist** | **Responsible & Accountable (`R/A`):** Triggers Batch 1 & Batch 2 matching, reviews 100% of matches & 3 exception queues, logs 1-click Override Reasons (`transfer_audit_log`), runs What-If policy simulations, and exports NEP `'Do Not Post'` staging CSVs. | **Stage 2 (`Exception-Only`):** Reviews only exception queues (`SMALL_CONTRACT_EXCEPTION`, `SM_EXTEND_SHIFT_REVIEW`, `DATE_GAP_REVIEW`).<br>**Stage 3 (`Bounded Autonomous`):** Monitors weekly 5% stratified QA samples & circuit breaker. | Accesses pseudonymous `STU-XXXX` & `SM-XXX` tokens only; zero raw names, emails, phones, DOBs, or NI numbers. |
| **Store Manager (`SM`)** *(470 UK Stores)* | **Consulted & Informed (`C/I`):** Submits Scratchpad shift availability (`Green Box` & `3-day CMP` flags) and queries Gemini Enterprise for candidate match explanations and Batch-2 CMP re-matches. | **Stage 2/3:** Receives automated shift-extension prompts (`+1.5hr` for `SM_EXTEND_SHIFT_REVIEW`) and real-time CMP clearance triggers via Gemini Enterprise. | Identified strictly as `SM-XXX` (`SM-001`..`SM-470`) scoped to `STORE-XXX`. |
| **Student Colleague** *(1,050+ Applicants)* | **Informed (`I`):** Submits transfer preferences (`Choice 1/2/3`, `available_days`, `preferred_latest_finish`, `target_transfer_date`, `small_contract_opt_in`) upstream. | **Stage 2/3:** Receives automated, grounded status explanations and 24h offer acceptance notifications without cold calls. | Tokenized upstream as `STU-XXXX` (`STU-0001`..`STU-1050`) + two-state `age_band` (`UNDER_18` / `ADULT_18_PLUS`). |
| **NEXT Enterprise IT / Platform Engineering** | **Accountable (`A` — Governance Gate):** Reviews `IT_HANDOVER_BUNDLE/` (WAF, SAIF, FinOps, `pytest` + `adk eval` attestations) and approves Gemini Enterprise publication. | Owns production Cloud Run, Global Load Balancer + Cloud IAP, and Stage 3 graduation policies in `next-ge-poc`. | Enforces `--no-allow-unauthenticated`, regional `europe-west2` Model Armor, and `roles/discoveryengine.admin` approval gate. |

---

## 3. Traceable Functional & Non-Functional Requirements

### 3.1 Functional Requirements (`FR-01`..`FR-08`)

| Req ID | Category | Requirement Specification | Verification Method |
| :--- | :--- | :--- | :--- |
| **`FR-01`** | **Deterministic Python** | **Eligibility Auto-Screen:** Admit only colleagues with `contract_type == "PERM"`, `star_rating in {"3", "4"}`, `review_up_to_date == "true"`, and `do_not_make_leaver == "true"`. Route `1*`/`2*`, `TEMP`, or stale-review applicants to `INELIGIBLE_ROUTED` with deterministic reason codes. | `100%` `pytest` invariant (`tests/test_matcher.py`) |
| **`FR-02`** | **Deterministic Python** | **Store Priority Ordering (Demand Pressure):** Process destination stores in descending order of total `Choice 1` applicant volume (`choice_1_demand DESC`, tie-broken by `store_id ASC`) so oversubscribed university hubs (Manchester, Leeds, Nottingham, Edinburgh) allocate scarce shifts fairly first. | `100%` `pytest` invariant |
| **`FR-03`** | **Deterministic Python** | **Candidate Ranking Within Store:** Rank eligible candidates within each store pass by `int(star_rating) DESC` (`4*` before `3*`), then `form_submitted_at ASC` (first-come, first-served), tie-broken by `student_id ASC`, while preserving `worked_at_choice_1_before` as a visible HR badge. | `100%` `pytest` invariant (`STU-0412` contention test) |
| **`FR-04`** | **Deterministic Python** | **Shift Guardrails (`Green Box` & `3-Day CMP`):** Strictly exclude any Scratchpad shift where `is_green_box_protected == "true"` or `cmp_3day_cleared == "false"`. Provide a `rerun_batch2_cmp_shifts` tool to re-match waitlisted students when Batch-2 shifts clear CMP (e.g., `SHF-9001..9003` at `STORE-019 Leeds Trinity`). | `100%` `pytest` invariant (`0` leaks allowed) |
| **`FR-05`** | **Deterministic Python** | **3-Pass Cascade (`Choice 1 -> 2 -> 3`), Unique-Availability First & Single-Store Lock:** Run a 3-pass cascade across `Choice 1`, `Choice 2`, and `Choice 3`. Within each store pass, allocate shifts with a single uniquely available candidate first before general ranked contention. Combine non-overlapping shifts (max 1 shift/day) **within a single destination store only**. Once assigned hours reach `>= 5.5` hrs/week, mark `MATCHED_COMPLETE`. | `100%` `pytest` invariant |
| **`FR-06`** | **Deterministic Python** | **Exception Queue Routing & What-If Simulator:** Route start-date gaps (`available_from > target_transfer_date`) to `DATE_GAP_REVIEW` (`STU-0305`), sub-`5.5hr` matches with `small_contract_opt_in == "true"` to `SMALL_CONTRACT_EXCEPTION` (`STU-0221`), and sub-`5.5hr` matches with `small_contract_opt_in == "false"` to `SM_EXTEND_SHIFT_REVIEW` (`STU-0222`). Provide a parameterized What-If simulator (`simulate_policy_change`) for HR policy testing. | `100%` `pytest` invariant + CLI simulation |
| **`FR-07`** | **ADK + Gemini (`gemini-2.5-flash`)** | **Grounded Conversational Explanations & Tool Orchestration:** Answer HR and Store Manager queries in Gemini Enterprise and Control Tower by invoking typed Python tools (`explain_student_status`, `run_batch_matching`, `rerun_batch2_cmp_shifts`, `simulate_policy_change`), citing exact `STU-XXXX`, `STORE-XXX`, and `SHF-XXXX` records with zero hallucinated IDs or hours. | `adk eval` Golden Set (`>=0.95` trajectory, `>=0.98` groundedness) |
| **`FR-08`** | **Self-Service Intake & Audit Loop** | **Stage 1 HITL Governance & NEP Export:** Provide a Self-Service Intake Portal (`ui/portal_intake.html`) and HR exception workflow displaying waterfall KPIs, the 3 HR exception queues, a 1-click Override Reason recorder (`transfer_audit_log`), a live Stage 1 $\rightarrow$ Stage 2 graduation readiness meter, and a staged NEP `'Do Not Post'` CSV export. | UI + API integration test |

### 3.2 Non-Functional Requirements (`NFR-01`..`NFR-05`)

| Req ID | Pillar | Requirement Specification | Target SLA / Gate |
| :--- | :--- | :--- | :--- |
| **`NFR-01`** | **Security (Model Armor Base State)** | Wire `ModelArmorGuard` (`before_model_callback`, `after_tool_callback`, `after_model_callback`) calling regional London **Model Armor** (`https://modelarmor.europe-west2.rep.googleapis.com`, template `next-student-transfer-armor`, [Model Armor Overview](https://docs.cloud.google.com/model-armor/overview)) plus deterministic local fallback (`ENABLE_MODEL_ARMOR=local`). | `100%` block rate on prompt injection & raw UK PII pastes |
| **`NFR-02`** | **Compliance (UK Under-18 & Zero PII)** | Enforce zero raw PII via `scan_dataset.py` pre-flight gate. Enforce UK Young Worker statutory rules ([GOV.UK Young Workers](https://www.gov.uk/rest-breaks-work/young-workers)) in deterministic Python for `UNDER_18`: **zero shifts ending after `22:00` or starting before `06:00`**, max **`8.0h/day`**, max **`40.0h/week`**. | `0` statutory breaches (`STU-0109` verified in `pytest`) |
| **`NFR-03`** | **FinOps & Performance** | Execute 1,050-student $\times$ 470-store matching in vectorized/in-memory Python (`O(N log N)`, `<1.0s` runtime, `$0.00` token cost). Ban row-by-row LLM loops (`>25` rows). Default to `gemini-2.5-flash` (`max_output_tokens <= 1024`) and Cloud Run `--min-instances=0 --max-instances=2 --cpu-throttling`. | `<$0.05` per batch run; `<$5/mo` total (`$15/seat/mo` GE pool) |
| **`NFR-04`** | **Operational Excellence** | Enforce automated `pytest` (100% pass bar) and a 50-scenario Golden Evaluation Set (`tests/eval_golden_set.json`) before Cloud Run deployment. Convert human overrides in `transfer_audit_log.jsonl` into regression eval cases. | `100%` `pytest` pass; `>=0.95` tool trajectory; `>=0.98` groundedness |
| **`NFR-05`** | **Enterprise IT Governance** | Deploy Cloud Run privately (`--no-allow-unauthenticated`) in `europe-west2`, grant `roles/run.invoker` only to the Discovery Engine service agent, and deliver `IT_HANDOVER_BUNDLE/` for NEXT IT promotion. | Zero hardcoded project IDs; 2-minute IT promotion runbook |

---

## 4. Zero-PII Gold Data Contracts (`students_gold.csv` & `store_shifts_gold.csv`)

Raw names, personal/work emails, UK phone numbers (`07...` / `+44...`), DOBs, postcodes, and NI numbers are strictly banned and blocked at ingestion by `scan_dataset.py`.

### 4.1 `data/students_gold.csv` (`1,050` Rows)
| Column | Type / Enum | Governance & Business Rule |
| :--- | :--- | :--- |
| `student_id` | `^STU-\d{4,}$` | Pseudonymous student token (`STU-0001`..`STU-1050`) |
| `age_band` | `UNDER_18` \| `ADULT_18_PLUS` | Replaces raw DOB; triggers `NFR-02` statutory night/hours hard block |
| `contract_type` | `PERM` \| `TEMP` | Must be `PERM` (`FR-01`) |
| `star_rating` | `1` \| `2` \| `3` \| `4` | Must be `3` or `4` (`FR-01`); primary sort key `DESC` (`FR-03`) |
| `review_up_to_date`, `do_not_make_leaver` | `true` \| `false` | Both must be `true` for eligibility (`FR-01`) |
| `home_store_id`, `choice_1..3_store_id` | `^STORE-\d{3}$` | Home store and 3 ranked destination store choices |
| `worked_at_choice_1_before` | `true` \| `false` | Prior store familiarity badge surfaced to HR/SM |
| `available_days` | Pipe-delimited `MON`..`SUN` | Allowed shift days (`e.g., SAT\|SUN`, `FRI\|SAT`) |
| `preferred_latest_finish` | `HH:MM` | Student finish cutoff (capped at `22:00` if `UNDER_18`) |
| `target_transfer_date` | `YYYY-MM-DD` | Compared against shift `available_from` for `DATE_GAP_REVIEW` (`FR-06`) |
| `form_submitted_at` | ISO-8601 UTC | Secondary tie-breaker `ASC` (`FR-03`) |
| `small_contract_opt_in` | `true` \| `false` | Routes `<5.5hr` partial fits to `SMALL_CONTRACT_EXCEPTION` vs `SM_EXTEND_SHIFT_REVIEW` |
| `home_store_release_status` | `PENDING_DESTINATION_ACCEPT` | Prevents premature home-store un-matching (`[^c2]`) |

### 4.2 `data/store_shifts_gold.csv` (`1,073` Shifts across `470` UK Stores)
| Column | Type / Enum | Governance & Business Rule |
| :--- | :--- | :--- |
| `shift_id` | `^SHF-\d{4,}$` | Unique Scratchpad shift identifier (`SHF-1001`.., `SHF-9001..9003`) |
| `store_id`, `store_name`, `region` | String | UK store metadata (`470` stores across 10 UK regions) |
| `manager_token` | `^SM-\d{3,}$` | Pseudonymous Store Manager token (`SM-001`..`SM-470`) |
| `day_of_week`, `start_time`, `end_time` | `MON`..`SUN`, `HH:MM` | Validated for overlap, `UNDER_18` (`06:00–22:00`), and `<=8.0h/day` |
| `weekly_hours` | Float (`4.0`..`7.5`) | Summed within a single store toward the `>=5.5hr` completion floor |
| `available_from` | `YYYY-MM-DD` | Triggers `DATE_GAP_REVIEW` if `> target_transfer_date` |
| `is_green_box_protected` | `true` \| `false` | Must be `false` (`FR-04` hard block) |
| `cmp_3day_cleared` | `true` \| `false` | Must be `true` (`FR-04` hard block; Batch 2 flips `SHF-9001..9003`) |
| `batch_wave` | `BATCH_1` \| `BATCH_2_PENDING_CMP` | Tracks Batch 1 vs Batch 2 CMP release waves |

---

## 5. Policy Ambiguities & Edge Cases Surfaced for Business Sign-Off (HR Policy Notes)

| Margin Flag | Ambiguity in Raw Notes | Deterministic Safe Handling in Phase 1 | Hero Test Case | Business Sign-Off Needed from HR Policy Lead |
| :--- | :--- | :--- | :--- | :--- |
| **`[^c1]` Start-Date Gap** | Store shift `available_from` is later than student's `target_transfer_date` (e.g., Oct 12 vs Sept 28). | Reserves shift so capacity isn't lost, but routes placement to **`DATE_GAP_REVIEW`** queue for HR/SM bridge sign-off. | **`STU-0305`** at `STORE-051 Edinburgh St James` (`2026-10-12` vs `2026-09-28`) | Approve max auto-match gap threshold (e.g., `<=7` days auto-match vs `>7` days review) for Stage 2. |
| **`[^c2]` Home-Store Shift Release Timing** | When to release the colleague's existing Home Store shifts without risking double-rostering or zero pay. | Sets `home_store_release_status = PENDING_DESTINATION_ACCEPT`; Home Store shifts are never released until NEP `'Do Not Post'` staging is approved. | All matched records (`STU-0018`, `STU-0412`, etc.) | Confirm whether Home Store release fires on SM offer acceptance or NEP contract effective date. |
| **`[^c3]` Sub-`5.5hr` Small Contracts** | Only a `4.0hr` shift remains (`<5.5hr` contract floor): allow student opt-in or ask SM to add `+1.5hr`? | Splits into two dedicated queues: **`SMALL_CONTRACT_EXCEPTION`** if `small_contract_opt_in == true`, or **`SM_EXTEND_SHIFT_REVIEW`** if `false`; includes What-If simulator for `4*` `4.0hr` policy impact. | **`STU-0221`** (`opt_in=true`, `STORE-012`) & **`STU-0222`** (`opt_in=false`, `STORE-019`) | Decide whether `4*` colleagues in oversubscribed cities (Manchester/Leeds) can auto-complete on `4.0hr` single shifts in Stage 2. |

---

## 6. 3-Stage Phased Launch, Headline KPIs & Earned Autonomy Exit Gates

- **Business Value Targets:** **80% reduction** in SM/HR manual admin time, **90% reduction** in NEP pay/small-contract tickets, and **100% elimination** of 30-row Google Sheet copy-pasting.

| Autonomy Stage | Operating Mode | Mandatory Evaluation & Governance Exit Gate to Graduate |
| :--- | :--- | :--- |
| **Stage 1 (Day-1 Launch)**<br>*100% HITL Co-Pilot* | Deterministic engine computes all matches & exceptions; the Central HR team reviews **100%** of placements before exporting NEP `'Do Not Post'` staging CSVs. 1-click overrides append to `transfer_audit_log`. | **Exit Gate to Stage 2:**<br>• `>= 500` student placements reviewed across `>= 2` consecutive batches.<br>• `>= 98.0%` unedited human approval rate on standard `MATCHED_COMPLETE` (`>=5.5hr`) placements.<br>• `0` statutory (`UNDER_18`) or policy (`Green Box`, `CMP`) breaches (`100%` `pytest` pass). |
| **Stage 2**<br>*Exception-Only HITL* | Clean `MATCHED_COMPLETE` (`>=5.5hr`, zero date gap) placements auto-stage to NEP `'Do Not Post'` batch; HR specialists review **only** the 3 exception queues (`SMALL_CONTRACT_EXCEPTION`, `SM_EXTEND_SHIFT_REVIEW`, `DATE_GAP_REVIEW`). | **Exit Gate to Stage 3:**<br>• `< 1.0%` defect rate in 5% weekly stratified HR QA sampling across a full seasonal window.<br>• `adk eval` Golden Set (`>= 50` cases) maintains `>= 0.95` tool trajectory & `>= 0.98` groundedness. |
| **Stage 3**<br>*Bounded Autonomous + Circuit Breaker* | Event-driven continuous matching as Scratchpad shifts clear 3-day CMP; automated offer dispatch with human oversight strictly on policy exceptions. | **Continuous Guardrail (Auto-Revert Circuit Breaker):**<br>• Cloud Monitoring alert auto-reverts system to **Stage 2** immediately if weekly human override rate exceeds **`3.0%`** or any `scan_dataset.py` / Model Armor critical alert triggers. |
