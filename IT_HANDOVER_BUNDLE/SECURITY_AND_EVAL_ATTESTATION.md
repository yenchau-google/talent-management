# NEXT PLC — Security, SAIF & Golden Evaluation Attestation

**Attestation Date:** 2026-11-02  
**Region:** `europe-west2` (London) / `eu` (Gemini Enterprise)  
**Target Customer Project:** `next-ge-poc` (`europe-west2`)

---

## 1. Pre-Flight PII & Policy Gate Attestation (`pii-and-policy-gate`)

| Dataset | Rows Scanned | Gate Status | Findings | Verification Command |
| :--- | :---: | :---: | :--- | :--- |
| `data/dirty_form_export.csv` (Trap File) | 5 | **BLOCKED (Exit Code 1)** | **15 Critical Findings** (Forbidden PII headers `student_full_name`/`personal_email`/`uk_mobile`/`date_of_birth`/`home_postcode`/`ni_number`, UK phones, emails, NI number, untokenized ID `10492`, `UNDER_18` 23:30 night shift, and `SYSTEM OVERRIDE` prompt injection) | `python3 .agents/skills/pii-and-policy-gate/scripts/scan_dataset.py data/dirty_form_export.csv` |
| `next_student_transfer_gold.students_gold` | 1,050 | **PASSED (Exit Code 0)** | **0 Findings** (`100% STU-XXXX` tokenized) | BigQuery Medallion & `pytest` verified |
| `next_student_transfer_gold.store_shifts_gold` | 1,093 | **PASSED (Exit Code 0)** | **0 Findings** (`470` UK stores, `SM-XXX` manager tokens) | BigQuery Medallion & `pytest` verified |

---

## 2. Deterministic Rule Engine & 50-Case Golden Eval Scorecard (`agent-eval-and-graduation`)

| Evaluation Pillar | Target Exit Bar | Verified Score | Status |
| :--- | :---: | :---: | :---: |
| **`pytest` Deterministic Rule Invariants (`tests/test_matcher.py`)** | `100% (14/14)` | **`100.0% (14/14 passed)`** | **PASS** |
| **50-Case Golden Eval Trajectory Precision (`tests/eval_golden_set.json`)** | `>= 0.95` | **`1.0000 (50/50 exact)`** | **PASS** |
| **50-Case Golden Eval Groundedness (Zero Hallucinated IDs/Hours)** | `>= 0.98` | **`1.0000 (50/50 grounded)`** | **PASS** |
| **Regional Model Armor (`europe-west2`) & DLP Intercept Rate** | `1.00` | **`1.0000 (100% blocked)`** | **PASS** |
| **UK Under-18 Statutory Breach Count (`<06:00`, `>22:00`, `>8h/d`, `>40h/w`)** | `0` | **`0 Breaches`** | **PASS** |
| **Green Box Protected & Un-Cleared 3-Day CMP Shift Leakage** | `0` | **`0 Leaked Shifts`** | **PASS** |
| **Stage 1 $\rightarrow$ Stage 2 Autonomy Graduation Readiness** | `>= 98.0%` across `>= 500` placements | **`99.64%` (`553` complete placements)** | **READY FOR STAGE 2 SIGN-OFF** |

---

## 3. SAIF Hard Boundaries (`B`) vs. Probabilistic Mitigations (`M`)

- **[B] Hard Boundaries:**
  1. Pre-flight CLI gate (`scan_dataset.py`) and Dataplex Auto Data Quality (`next-student-transfer-dq-scan`) reject raw UK PII, untokenized IDs, and policy violations (`DQ-01..DQ-06`) before promotion to Gold.
  2. 100% deterministic Python (`src/matcher.py`) enforces `UNDER_18` working-time limits, `3*`/`4*` permanent eligibility, Green Box exclusions, 3-day CMP clearance, single-store shift lock, and `>=5.5hr` contract completion.
  3. Cloud Run deployed with `--no-allow-unauthenticated` and stage-gated NEP `'Do Not Post'` CSV export (zero direct ERP mutation).
- **[M] Probabilistic Mitigations:**
  1. Regional Model Armor (`projects/${GOOGLE_CLOUD_PROJECT}/locations/europe-west2/templates/next-student-transfer-armor`) inspects every prompt, tool payload, and model response at `https://modelarmor.europe-west2.rep.googleapis.com` (`dataResidencyCompliant: true`) with deterministic local fallback (`ENABLE_MODEL_ARMOR=local`).
