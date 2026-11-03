# NEXT PLC — Student Transfer Assistant: Solution Design (`02-solution-design.md`)

## 1. Architecture Overview & Design Principles (Phase 2 Medallion & Enterprise UX Architecture)

The **NEXT Student Transfer Assistant** uses a **Two-App Segregated Cloud Run + Two-Dataset BigQuery Medallion + Dataplex Auto Data Quality + Multi-Agent ADK Architecture** deployed in `europe-west2` (London) on Google Cloud (`talent-management-513520` for CE showcase; `IT_HANDOVER_BUNDLE/` for NEXT IT promotion to `next-ge-poc`).

1. **Two-Dataset BigQuery Medallion Architecture (`europe-west2`, Zero Golden CSVs):**
   - **Bronze Intake & DQ Quarantine (`talent-management-513520.next_student_transfer_staging`):**
     - `portal_change_requests`: Append-only `INSERT`s from App 1 (`<200ms`) with denormalized flat columns (`request_id`, `submitted_at`, `actor_role`, `actor_id`, `target_table`, `record_id`, `age_band`, `choice_1`, `choice_2`, `choice_3`, `transfer_gap_days`, `shift_hours`, `preferred_latest_finish`, `was_green_box_protected`, `new_is_green_box_protected`, `cmp_3day_cleared`, `note`, `promotion_status` IN `PROMOTED` | `QUARANTINED` | `REJECTED_BY_HR` | `OVERRIDDEN_BY_HR`).
     - `change_audit_log`: Strictly append-only event log (never `DELETE` or `TRUNCATE`; resetting demo data appends a `DEMO_RESET` audit row).
     - `v_data_quality_warnings`: `0ms` real-time SQL view evaluating `DQ-01..DQ-06` across active `QUARANTINED` rows in `portal_change_requests`.
   - **Trusted Gold & Matching (`talent-management-513520.next_student_transfer_gold`):**
     - `students_gold` (`1,050` tokenized students seeded via SQL), `store_shifts_gold` (`1,093` shifts across `470` UK stores seeded via SQL), `students_gold_baseline`, `store_shifts_gold_baseline`, `placements_staging`, `transfer_audit_log`, `outreach_email_log`, `v_eligible_shifts_sql`, and `sp_simulate_store_capacity`.
     - **Promotion & Quarantine Rule:** Valid App 1 edits (Scenario 1: `SM-019` clearing `SHF-9001..9003` 3-day CMP) promote into `next_student_transfer_gold.store_shifts_gold` (`promotion_status = 'PROMOTED'`). Invalid App 1 edits (`DQ-01..DQ-06`) remain quarantined in `portal_change_requests` (`promotion_status = 'QUARANTINED'`) until HR calls `resolve_quarantined_change(request_id, decision='REJECT' | 'OVERRIDE_PROMOTE', hr_reason, confirm=True)` — keeping `*_gold` clean at all times.
2. **Real Dataplex Auto Data Quality (`next-student-transfer-dq-scan`) + `0ms` SQL View Together:**
   - Provisioned `projects/talent-management-513520/locations/europe-west2/dataScans/next-student-transfer-dq-scan` targeting `next_student_transfer_staging.portal_change_requests` (`config/dataplex_dq_rules.yaml`).
   - App 1 triggers `dataScans:run` asynchronously on every Scenario Button or manual save so real Dataplex scan jobs populate the GCP Console UI, while `dq_and_change_auditor_agent` queries `v_data_quality_warnings` (`0ms`) + latest completed `DataScan` job metadata so live demo turns never wait 30–60s for batch scan startup.
3. **Two Strictly Segregated Cloud Run Services (`europe-west2`) + Custom Domain Global Load Balancer:**
   - **Cloud Run App 1 (`next-portal-intake-app` — [`src/portal_server.py`](../../src/portal_server.py) + [`ui/portal_intake.html`](../../ui/portal_intake.html)):** Student (`STU-XXXX`) & Store Manager (`SM-XXX`) Self-Service Portal using the **Modern High-Street Retail Monochrome (`NEXT`)** design system (`Plus Jakarta Sans` + `JetBrains Mono` `.tabular`, `#F7F7F5` warm alabaster canvas, `#FFFFFF` workspace, `#111111` primary action, `4px` radius), header badge `[PHASE 2 SELF-SERVICE PORTAL PREVIEW — PHASE 1 COMPATIBLE WITH GOOGLE SHEETS]`, and a collapsible bottom-right Presenter Stage Dock (`DEMO_MODE=true`). Deployed with `--no-allow-unauthenticated` and fronted by a **Global External Application Load Balancer + Google-Managed SSL Certificate + Cloud IAP** at **`https://next-portal.yenchau.demo.altostrat.com/ui`** ([`scripts/setup_custom_domain_lb.sh`](../../scripts/setup_custom_domain_lb.sh)).
   - **Cloud Run App 2 (`next-student-transfer-agent` — [`src/server.py`](../../src/server.py) + [`src/agent.py`](../../src/agent.py)):** Pure Headless HR Multi-Agent A2A Backend (`POST /a2a/app`) registered onto **Gemini Enterprise (`gemini-enterprise-retail-app`)**, deployed with `--no-allow-unauthenticated --no-cpu-throttling --cpu-boost --min-instances=1 --max-instances=2` and protected by Regional `europe-west2` **Model Armor** (`before_model_callback`, `after_tool_callback`, `after_model_callback`).
4. **3-Sub-Agent ADK Hierarchy + Gemini Enterprise Native `@Gmail` Connector Handoff:**
   - Root Coordinator (`next_hr_transfer_coordinator`) delegates across:
     1. `dq_and_change_auditor_agent` (`audit_recent_changes_and_dq_warnings`, `resolve_quarantined_change`, `revert_invalid_change`)
     2. `matcher_and_simulator_agent` (`run_batch_matching`, `explain_student_status`, `simulate_policy_change`, `rerun_batch2_cmp_shifts`)
     3. `hr_comms_and_intervention_agent` (`generate_and_send_comms_strategy`, `record_hr_exception_decision`)
   - Stages tokenized outreach drafts in `next_student_transfer_gold.outreach_email_log` (`delivery_status = 'STAGED_FOR_GE_GMAIL'`) using regex-verified `+` alias addresses (`admin+stu-0412@yenchau.altostrat.com`, `admin+sm-012@yenchau.altostrat.com`) and outputs copy-clean `To / Subject / Body` blocks for a **2-turn native `@Gmail` Connector handoff** in Gemini Enterprise.

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
        GEApp --> GmailConn["Turn 2: Native @Gmail Connector Handoff<br/>Tokenized + Alias Routing (admin+stu-0412@ / admin+sm-012@)"]
    end
```

---

## 2. Component Design & Requirement Traceability Matrix (`FR-01`..`FR-08`, `NFR-01`..`NFR-05`)

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

## 3. Accepted Demo Waivers vs. NEXT IT Production Target State (`next-ge-poc`)

| Area | Argolis Live Showcase Implementation (`talent-management-513520`) | NEXT IT Production Target State (`next-ge-poc`) |
| :--- | :--- | :--- |
| **1. Service Account Separation** | Single Argolis default compute service account (`537939007190-compute@developer.gserviceaccount.com`) used across App 1 and App 2 to avoid Org Policy SA key/creation blocks during rapid showcase provisioning. | Two dedicated least-privilege service accounts: `sa-portal-intake@next-ge-poc.iam.gserviceaccount.com` (write-only to `next_student_transfer_staging.portal_change_requests`, read-only on `*_gold`) and `sa-hr-agent@next-ge-poc.iam.gserviceaccount.com` (read/write on `next_student_transfer_staging` & `next_student_transfer_gold` + `roles/modelarmor.user`). |
| **2. Agent Runtime Hosting** | Cloud Run App 2 (`next-student-transfer-agent`, `europe-west2`) hosting the FastAPI `/a2a/app` A2A v0.3 endpoint with `--min-instances=1 --no-cpu-throttling --cpu-boost`. | Managed **Vertex AI Agent Engine (`europe-west2`, GA)** or private Cloud Run with VPC-SC perimeter and Workforce Identity Federation. |
| **3. Self-Service Persona Switching** | Collapsible bottom-right Presenter Stage Dock (`DEMO_MODE=true`) allowing 1-key (`1`, `2`, `3`, `R`, `` ` ``) switching between `STU-0412`, `STU-0109`, `SM-019`, and `SM-012` behind Cloud Run native IAP (`user:admin@yenchau.altostrat.com`). | `DEMO_MODE=false` in production; colleague identity (`STU-XXXX` / `SM-XXX`) derived strictly from the signed IAP JWT assertion (`X-Goog-IAP-JWT-Assertion`) mapped to NEXT PingFederate / Entra ID SSO. |
