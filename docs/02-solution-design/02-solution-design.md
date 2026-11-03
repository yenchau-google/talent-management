# NEXT PLC — Student Transfer Assistant: Solution Design (`02-solution-design.md`)

## 1. Architecture Overview & Design Principles (Phase 2 Medallion & Enterprise UX Architecture)

The **NEXT Student Transfer Assistant** uses a **Two-App Segregated Cloud Run + Two-Dataset BigQuery Medallion + Dataplex Auto Data Quality + Multi-Agent ADK Architecture** deployed in `europe-west2` (London) on Google Cloud (`${GOOGLE_CLOUD_PROJECT}`; `IT_HANDOVER_BUNDLE/` for NEXT IT promotion to `next-ge-poc`).

1. **Two-Dataset BigQuery Medallion Architecture (`europe-west2`, Zero Golden CSVs):**
   - **Bronze Intake & DQ Quarantine (`${GOOGLE_CLOUD_PROJECT}.next_student_transfer_staging`):**
     - `portal_change_requests`: Append-only `INSERT`s from App 1 (`<200ms`) with denormalized flat columns (`request_id`, `submitted_at`, `actor_role`, `actor_id`, `target_table`, `record_id`, `age_band`, `choice_1`, `choice_2`, `choice_3`, `transfer_gap_days`, `shift_hours`, `preferred_latest_finish`, `was_green_box_protected`, `new_is_green_box_protected`, `cmp_3day_cleared`, `note`, `promotion_status` IN `PROMOTED` | `QUARANTINED` | `REJECTED_BY_HR` | `OVERRIDDEN_BY_HR`).
     - `change_audit_log`: Strictly append-only event log (never `DELETE` or `TRUNCATE`; resetting demo data appends a `DEMO_RESET` audit row).
     - `v_data_quality_warnings`: `0ms` real-time SQL view evaluating `DQ-01..DQ-06` across active `QUARANTINED` rows in `portal_change_requests`.
   - **Trusted Gold & Matching (`${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold`):**
     - `students_gold` (`1,050` tokenized students seeded via SQL), `store_shifts_gold` (`1,093` shifts across `470` UK stores seeded via SQL), `students_gold_baseline`, `store_shifts_gold_baseline`, `placements_staging`, `transfer_audit_log`, `outreach_email_log`, `v_eligible_shifts_sql`, and `sp_simulate_store_capacity`.
     - **Promotion & Quarantine Rule:** Valid App 1 edits (Scenario 1: `SM-019` clearing `SHF-9001..9003` 3-day CMP) promote into `next_student_transfer_gold.store_shifts_gold` (`promotion_status = 'PROMOTED'`). Invalid App 1 edits (`DQ-01..DQ-06`) remain quarantined in `portal_change_requests` (`promotion_status = 'QUARANTINED'`) until HR calls `resolve_quarantined_change(request_id, decision='REJECT' | 'OVERRIDE_PROMOTE', hr_reason, confirm=True)` — keeping `*_gold` clean at all times.
2. **Real Dataplex Auto Data Quality (`next-student-transfer-dq-scan`) + `0ms` SQL View Together:**
   - Provisioned `projects/${GOOGLE_CLOUD_PROJECT}/locations/europe-west2/dataScans/next-student-transfer-dq-scan` targeting `next_student_transfer_staging.portal_change_requests` (`config/dataplex_dq_rules.yaml`).
   - App 1 triggers `dataScans:run` asynchronously on every Scenario Button or manual save so real Dataplex scan jobs populate the GCP Console UI, while `dq_and_change_auditor_agent` queries `v_data_quality_warnings` (`0ms`) + latest completed `DataScan` job metadata so live demo turns never wait 30–60s for batch scan startup.
3. **Two Strictly Segregated Cloud Run Services (`europe-west2`) + Custom Domain Global Load Balancer:**
   - **Cloud Run App 1 (`next-portal-intake-app` — [`src/portal_server.py`](../../src/portal_server.py) + [`ui/portal_intake.html`](../../ui/portal_intake.html)):** Student (`STU-XXXX`) & Store Manager (`SM-XXX`) Self-Service Portal using the **Modern High-Street Retail Monochrome (`NEXT`)** design system (`Plus Jakarta Sans` + `JetBrains Mono` `.tabular`, `#F7F7F5` warm alabaster canvas, `#FFFFFF` workspace, `#111111` primary action, `4px` radius), header badge `[PHASE 2 SELF-SERVICE PORTAL PREVIEW — PHASE 1 COMPATIBLE WITH GOOGLE SHEETS]`, and a collapsible bottom-right Presenter Stage Dock (`DEMO_MODE=true`). Deployed with `--no-allow-unauthenticated` and fronted by a **Global External Application Load Balancer + Google-Managed SSL Certificate + Cloud IAP** ([`scripts/setup_custom_domain_lb.sh`](../../scripts/setup_custom_domain_lb.sh)).
   - **Cloud Run App 2 (`next-student-transfer-agent` — [`src/server.py`](../../src/server.py) + [`src/agent.py`](../../src/agent.py)):** Pure Headless HR Multi-Agent A2A Backend (`POST /a2a/app`) registered onto **Gemini Enterprise (`gemini-enterprise-retail-app`)**, deployed with `--no-allow-unauthenticated --no-cpu-throttling --cpu-boost --min-instances=1 --max-instances=2` and protected by Regional `europe-west2` **Model Armor** (`before_model_callback`, `after_tool_callback`, `after_model_callback`).
4. **3-Sub-Agent ADK Hierarchy + Gemini Enterprise Native `@Gmail` Connector Handoff:**
   - Root Coordinator (`next_hr_transfer_coordinator`) delegates across:
     1. `dq_and_change_auditor_agent` (`audit_recent_changes_and_dq_warnings`, `resolve_quarantined_change`, `revert_invalid_change`)
     2. `matcher_and_simulator_agent` (`run_batch_matching`, `explain_student_status`, `simulate_policy_change`, `rerun_batch2_cmp_shifts`)
     3. `hr_comms_and_intervention_agent` (`generate_and_send_comms_strategy`, `record_hr_exception_decision`)
   - Stages tokenized outreach drafts in `next_student_transfer_gold.outreach_email_log` (`delivery_status = 'STAGED_FOR_GE_GMAIL'`) using regex-verified `+` alias addresses (`hr-notifications+stu-0412@next-retail.example.com`, `hr-notifications+sm-012@next-retail.example.com`) and outputs copy-clean `To / Subject / Body` blocks for a **2-turn native `@Gmail` Connector handoff** in Gemini Enterprise.

```mermaid
flowchart TB
    subgraph App1["1. Cloud Run App 1: Colleague & Store Manager Portal (next-portal-intake-app + Cloud Run IAP)"]
        UI1["NEXT Monochrome Retail UI (ui/portal_intake.html)<br/>• Student Guided Flow + 21-Day Continuity Bar<br/>• Store Manager Open-Canvas Roster Table<br/>• Collapsible Presenter Stage Dock (Keys ` / 1-3 / R)"] --> PortalAPI["FastAPI Portal Backend (src/portal_server.py)<br/>Parameterized BigQuery DML + Async Dataplex Trigger"]
    end

    subgraph BQ["2. BigQuery Medallion & Dataplex AutoDQ (europe-west2)"]
        PortalAPI --> Staging["Bronze Quarantine (next_student_transfer_staging)<br/>• portal_change_requests (PROMOTED vs QUARANTINED)<br/>• change_audit_log (Append-Only)<br/>• v_data_quality_warnings (0ms SQL View)"]
        Staging --> DQ["Dataplex Auto DQ Scan (next-student-transfer-dq-scan)<br/>DQ-01..DQ-06 Rules"]
        PortalAPI -->|Valid Edits Only (PROMOTED)| Gold["Trusted Gold (next_student_transfer_gold)<br/>• students_gold (1,050) • store_shifts_gold (1,093)<br/>• placements_staging • transfer_audit_log<br/>• outreach_email_log • sp_simulate_store_capacity"]
    end

    subgraph App2["3. Cloud Run App 2: Headless HR Multi-Agent A2A Backend (next-student-transfer-agent)"]
        Staging & Gold --> Matcher["Deterministic Python & BigQuery SQL Engine<br/>3-Pass >=5.5h Cascade | <18 <=22:00 & Green Box Guards"]
        Matcher --> RootADK["ADK Root Coordinator (next_hr_transfer_coordinator)<br/>• dq_and_change_auditor_agent<br/>• matcher_and_simulator_agent<br/>• hr_comms_and_intervention_agent"]
        Armor["Regional ModelArmorGuard (europe-west2)<br/>before_model + after_tool + after_model"] <--> RootADK
    end

    subgraph GE["4. Gemini Enterprise (gemini-enterprise-retail-app)"]
        GEApp["HR Conversational Control Plane (/a2a/app A2A v0.3)<br/>A2A-Safe HITL (confirm: bool = False -> True)"] <--> Armor
        GEApp --> GmailConn["Turn 2: Native @Gmail Connector Handoff<br/>Tokenized + Alias Routing (hr-notifications+stu-0412@)"]
    end
```

---

## 2. Deterministic Matching Engine & What-If Simulation Specification ([`src/matcher.py`](../../src/matcher.py) + [`sql/03_matching_and_simulation_procs.sql`](../../sql/03_matching_and_simulation_procs.sql))

In accordance with **Rule 02** (ban on row-by-row LLM loops `>25` rows) and **Rule 03** (`100%` deterministic Python math for eligibility, `<18` working-time bounds, ranking, and `>=5.5hr` shift combinations), the matching engine is implemented as a pure, side-effect-free Python module ([`src/matcher.py`](../../src/matcher.py)) backed by BigQuery Gold tables and stored procedures ([`sql/03_matching_and_simulation_procs.sql`](../../sql/03_matching_and_simulation_procs.sql)).

### 2.1 Core Data Structures & Complexity
- **Input Cardinality:** $N = 1,050$ tokenized student rows (`next_student_transfer_gold.students_gold`) and $S = 1,093$ shift rows across $M = 470$ UK retail stores (`next_student_transfer_gold.store_shifts_gold`).
- **Time & Space Complexity:** **`O(M log M + N log N)`** (`< 50ms` in-memory execution; `0` LLM tokens consumed during matching).
- **Output Data Contract ([`MatchDecision`](../../src/matcher.py#L46-L61)):**
  - `student_id`, `status` $\in$ `{MATCHED_COMPLETE, DATE_GAP_REVIEW, SMALL_CONTRACT_EXCEPTION, SM_EXTEND_SHIFT_REVIEW, UNMATCHED_WAITLIST, INELIGIBLE_ROUTED}`
  - `matched_store_id`, `matched_store_name`, `matched_choice_rank` $\in \{1, 2, 3\}$, `allocated_shift_ids`, `allocated_weekly_hours`, `unique_slot_priority_used`
  - `under18_Blocked_shifts` (audit trail of every shift rejected due to UK Young Worker law)
  - `contended_out_history` (provenance list of higher-ranked `STU-XXXX` colleagues who claimed the last compatible shifts at Choice 1/2/3 stores)
  - `regional_vacant_alternatives` (same-region stores with `>= 5.5h` remaining CMP-cleared, non-Green-Box capacity)

### 2.2 The 7-Step Algorithmic Pipeline
1. **Step 1 — Eligibility Gate ([`check_eligibility`](../../src/matcher.py#L81-L97)):**
   - Checks `contract_type == "PERM"`, `star_rating in ("3", "4")`, `review_up_to_date == "true"`, and `do_not_make_leaver == "true"`.
   - Fails closed to `INELIGIBLE_ROUTED` (`128` rows in baseline) with explicit human-readable rejection reasons.
2. **Step 2 — Store Demand Priority Sort ([`run_matching_engine`](../../src/matcher.py#L228-L234)):**
   - Aggregates Choice-1 demand per store across all `1,050` rows and sorts stores by `(-choice_1_count, store_id)`.
3. **Step 3 — 3-Pass Cascade (`Choice 1 -> Choice 2 -> Choice 3`) & Fairness Tie-Breaking ([`src/matcher.py`](../../src/matcher.py#L244-L261)):**
   - Iterates `pass_num` $\in \{1, 2, 3\}$. For each store in demand order, filters unplaced eligible applicants requesting that store in `choice_{pass_num}_store_id` and sorts by `(-int(star_rating), form_submitted_at ASC, student_id ASC)`.
4. **Step 4 — Shift-Level Statutory & Governance Guards ([`is_shift_compatible_for_student`](../../src/matcher.py#L100-L133)):**
   - Hard-blocks shifts where `is_green_box_protected == "true"` (unless `allow_green_box=True` in simulation) or `cmp_3day_cleared == "false"` (unless `include_un_cleared_cmp=True` in simulation).
   - Verifies `shift.day_of_week_slot` $\in$ `student.available_day_slots` and `end_time <= student.preferred_latest_finish`.
   - For `age_band == "UNDER_18"`, enforces UK Young Worker statutory rules ([GOV.UK](https://www.gov.uk/rest-breaks-work/young-workers)): `start_time >= "06:00"`, `end_time <= "22:00"`, `weekly_hours <= 8.0`, and `current_accumulated_hours + weekly_hours <= 40.0`.
5. **Step 5 — Two-Subpass Allocation (`Unique-Availability-First` $\rightarrow$ `Standard Fill`) & Single-Store `>= 5.5hr` Lock ([`attempt_store_allocation`](../../src/matcher.py#L136-L185)):**
   - **Subpass A (`only_unique_slot_Pass=True`):** Allocates students who have only **1** compatible day slot at that store first (`unique_slot_priority_used = True`).
   - **Subpass B (`only_unique_slot_Pass=False`):** Greedily combines open shifts in the **same store** (never splitting across stores, max 1 shift per day slot, ordered by `(-weekly_hours, shift_id)`) until `total_hours >= min_hours_threshold` (`5.5h`).
   - **Zero-Consumption Rollback:** If combined compatible shifts at that store sum to `< 5.5h`, zero shifts are consumed during Passes 1–3 so partial shifts remain available for other candidates.
   - **21-Day Continuity Check:** Placed students with `transfer_gap_days > 21` are routed to `DATE_GAP_REVIEW` (`27` rows); those with `<= 21` days are routed to `MATCHED_COMPLETE` (`377` rows).
6. **Step 6 — Contention Provenance & Post-Cascade Exception Triage ([`src/matcher.py`](../../src/matcher.py#L291-L398)):**
   - Logs `contended_out_history` whenever a candidate had compatible shifts at a store at the start of the pass that were consumed by higher-ranked candidates during that pass.
   - After Pass 3, inspects remaining `< 5.5h` open shifts across each unplaced student's Choice 1/2/3 stores to classify into:
     - `SMALL_CONTRACT_EXCEPTION` (`81` rows): `4.0h–5.4h` shift where `prefer_small_4hr_contract == "true"` (e.g., `STU-0221`).
     - `SM_EXTEND_SHIFT_REVIEW` (`64` rows): `4.0h–5.4h` shift where a Store Manager `+1.5h` extension reaches `5.5h` (e.g., `STU-0222`).
     - `UNMATCHED_WAITLIST` (`373` rows): Attaches top 3 **Regional Cluster Vacant Stores** in the student's `region` with `>= 5.5h` open CMP-cleared capacity.
7. **Step 7 — Dual-Engine What-If Simulation ([`run_what_if_simulation`](../../src/matcher.py#L440-L505) & BigQuery [`sp_simulate_store_capacity`](../../sql/03_matching_and_simulation_procs.sql#L26-L120)):**
   - Executes side-by-side baseline vs. simulated runs in memory and in BigQuery (`CALL next_student_transfer_gold.sp_simulate_store_capacity(min_hours, include_green_box, region_filter)`) to quantify exact placement uplift (`+10` additional `4*` students placed in Manchester & Leeds when relaxing `5.5h` $\rightarrow$ `4.0h`) without mutating Gold tables.

---

## 3. Component Design & Requirement Traceability Matrix (`FR-01`..`FR-08`, `NFR-01`..`NFR-05`)

| Req ID | Module / File | Technical Implementation Details |
| :--- | :--- | :--- |
| **`FR-01`** | [`src/matcher.py`](../../src/matcher.py) (`check_eligibility`) | Evaluates `contract_type == "PERM"`, `star_rating in ("3", "4")`, `review_up_to_date == "true"`, `do_not_make_leaver == "true"`. Non-qualifying rows return `status = "INELIGIBLE_ROUTED"`. |
| **`FR-02`** | [`src/matcher.py`](../../src/matcher.py) (`run_matching_engine`) | Counts `choice_1_store_id` across all `1,050` `next_student_transfer_gold.students_gold` rows and sorts stores by `(-choice_1_count, store_id)`. |
| **`FR-03`** | [`src/matcher.py`](../../src/matcher.py) | Sorts eligible candidates requesting a store in pass $p \in \{1, 2, 3\}$ by `(-int(star_rating), form_submitted_at, student_id)`. Records `contended_out_history` provenance. |
| **`FR-04`** | [`src/matcher.py`](../../src/matcher.py), [`sql/03_matching_and_simulation_procs.sql`](../../sql/03_matching_and_simulation_procs.sql) | Hard-blocks shifts where `is_green_box_protected == "true"`, `cmp_3day_cleared == "false"`, or `weekly_hours < 4.0` / `> 12.0` (`DQ-03`), and quarantines unapproved Green Box unlocks (`DQ-05`) in `next_student_transfer_staging.portal_change_requests`. |
| **`FR-05`** | [`src/matcher.py`](../../src/matcher.py) (`run_matching_engine`) | Executes Pass 1 (`choice_1_store_id`), Pass 2 (`choice_2_store_id`), and Pass 3 (`choice_3_store_id`) with Unique-Availability-First and Single-Store `>=5.5h` lock, staging results to `next_student_transfer_gold.placements_staging`. |
| **`FR-06`** | [`src/matcher.py`](../../src/matcher.py), [`sql/03_matching_and_simulation_procs.sql`](../../sql/03_matching_and_simulation_procs.sql) | Routes exceptions to `SMALL_CONTRACT_EXCEPTION` (`STU-0221`), `SM_EXTEND_SHIFT_REVIEW` (`STU-0222`), and `DATE_GAP_REVIEW` (`STU-0305`), and runs `sp_simulate_store_capacity` in BigQuery. |
| **`FR-07`** | [`src/agent.py`](../../src/agent.py), [`src/server.py`](../../src/server.py) | Defines `root_agent` (`next_hr_transfer_coordinator`) and 3 specialized ADK sub-agents (`dq_and_change_auditor_agent`, `matcher_and_simulator_agent`, `hr_comms_and_intervention_agent`) exposed over A2A JSON-RPC 2.0 (`POST /a2a/app`). |
| **`FR-08`** | [`src/portal_server.py`](../../src/portal_server.py), [`src/bq_repository.py`](../../src/bq_repository.py), [`src/agent.py`](../../src/agent.py) | Segregates Student/Store Manager self-service edits (App 1) from HR quarantine resolution (`resolve_quarantined_change(..., confirm=False -> True)`), HITL override logging (`record_hr_exception_decision(..., confirm=False -> True)`), and native `@Gmail` outreach staging (`generate_and_send_comms_strategy`). |
| **`NFR-01`** | [`src/model_armor_guard.py`](../../src/model_armor_guard.py) | Calls `https://modelarmor.europe-west2.rep.googleapis.com` (`next-student-transfer-armor`, `<256 KB` cap, `800ms` timeout) + deterministic URL/Markdown stripper across `before_model_callback`, `after_tool_callback`, and `after_model_callback`. |
| **`NFR-02`** | [`src/matcher.py`](../../src/matcher.py), [`config/dataplex_dq_rules.yaml`](../../config/dataplex_dq_rules.yaml) | Enforces `start_time >= "06:00"`, `end_time <= "22:00"`, `weekly_hours <= 8.0` per shift, and `DQ-06` Dataplex Data Quality quarantine for `UNDER_18`. |
| **`NFR-03`** | [`Dockerfile`](../../Dockerfile), [`Dockerfile.portal`](../../Dockerfile.portal) | Zero LLM calls inside loops, hardened non-root containers (`appuser`), `.dockerignore`/`.gcloudignore` context minimization, App 1 `--min-instances=0`, and App 2 `--min-instances=1 --max-instances=2 --no-cpu-throttling --cpu-boost`. |

---

## 4. Accepted Demo Waivers vs. NEXT IT Production Target State (`next-ge-poc`)

| Area | Showcase / PoC Implementation (`${GOOGLE_CLOUD_PROJECT}`) | NEXT IT Production Target State (`next-ge-poc`) |
| :--- | :--- | :--- |
| **1. Service Account Separation** | Single project compute service account used across App 1 and App 2 during rapid PoC provisioning. | Two dedicated least-privilege service accounts: `sa-portal-intake@next-ge-poc.iam.gserviceaccount.com` (write-only to `next_student_transfer_staging.portal_change_requests`, read-only on `*_gold`) and `sa-hr-agent@next-ge-poc.iam.gserviceaccount.com` (read/write on `next_student_transfer_staging` & `next_student_transfer_gold` + `roles/modelarmor.user`). |
| **2. Agent Runtime Hosting** | Cloud Run App 2 (`next-student-transfer-agent`, `europe-west2`) hosting the FastAPI `/a2a/app` A2A v0.3 endpoint with `--min-instances=1 --no-cpu-throttling --cpu-boost`. | Managed **Vertex AI Agent Engine (`europe-west2`, GA)** or private Cloud Run with VPC-SC perimeter and Workforce Identity Federation. |
| **3. Self-Service Persona Switching** | Collapsible bottom-right Presenter Stage Dock (`DEMO_MODE=true`) allowing 1-key (`1`, `2`, `3`, `R`, `` ` ``) switching between `STU-0412`, `STU-0109`, `SM-019`, and `SM-012` behind Cloud IAP. | `DEMO_MODE=false` in production; colleague identity (`STU-XXXX` / `SM-XXX`) derived strictly from the signed IAP JWT assertion (`X-Goog-IAP-JWT-Assertion`) mapped to NEXT PingFederate / Entra ID SSO. |
