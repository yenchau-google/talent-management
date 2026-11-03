# NEXT PLC — Student Transfer Lighthouse Workspace (`Antigravity` + `Gemini Enterprise`)

This workspace contains the **Antigravity (`AGY`) Governance Rules, 7 Reusable Skills, Scoping & Solution Design Artefacts, BigQuery Medallion SQL, and Production Multi-Agent Code** for building and registering the **NEXT Student Transfer Assistant** on Google Cloud (`europe-west2`) and **Gemini Enterprise**.

## Workspace Structure
- `.env.example` — Copy to `.env` and set `GOOGLE_CLOUD_PROJECT` (never hardcode project IDs in source code).
- `.agents/rules/` — 3 always-on guardrails enforced on every prompt and code edit:
  1. `01-pii-and-minor-protection.md` (`STU-XXXX` tokenization, zero UK PII, UK Under-18 working-time hard blocks)
  2. `02-finops-and-cost-control.md` (`gemini-2.5-flash` default, zero row-by-row LLM loops `>25` rows, Cloud Run scale-to-zero)
  3. `03-deterministic-math-armor-and-hitl.md` (100% Python math + `pytest` for `>=5.5hr`/ranking/CMP/GreenBox, regional `europe-west2` **Model Armor** Day-1 Base State, HITL staging only)
- `.agents/skills/` — 7 on-demand skills:
  1. `workflow-scoper` — Turns messy operational notes into `FR-*`/`NFR-*` requirements, 3-Persona RACI, Gold Schemas, 3-Stage Autonomy KPIs, and Policy Ambiguity Flags.
  2. `pii-and-policy-gate` — Runs `scripts/scan_dataset.py` to block raw PII, prompt-injection strings, and Under-18/GreenBox/CMP schema errors before build.
  3. `gcp-waf-advisor` — Reviews solution designs against the 6 official Google Cloud Well-Architected Framework pillars + AI/ML perspective (`WAF-NN` findings).
  4. `saif-security-advisor` — Threat-models agent designs against Google's Secure AI Framework (15 risks, 24 controls, Agent Extension, Hard Boundary vs Mitigation).
  5. `finops-cost-estimator` — Audits code for token-loop anti-patterns and sizes cost against Gemini Enterprise pooled developer quota (`$15/seat/mo`).
  6. `agent-eval-and-graduation` — Builds the 50-case Golden Eval Set, runs `pytest` + `adk eval`, and calculates Stage 1 `-->` Stage 2 `-->` Stage 3 autonomy readiness.
  7. `ge-release-packager` — Deploys private Cloud Run + regional Model Armor, publishes to Gemini Enterprise, and generates `IT_HANDOVER_BUNDLE/` for NEXT Enterprise IT (`roles/discoveryengine.admin`).
- `inputs/next_student_transfer_raw_notes.md` — Raw operational process & project plan notes for Phase 1 (`workflow-scoper`).
- `docs/01-scoping/` — `01-scoping-requirements.md` (`FR-01..08`, `NFR-01..05`, 3-Persona RACI, Tokenized Gold Schemas, 3-Stage Autonomy KPIs, and Policy Ambiguity Flags).
- `docs/02-solution-design/` — `02-solution-design.md` (Technical Architecture & ADK Multi-Agent Spec), `03-waf-review.md` (Google Cloud Well-Architected + FinOps Review), and `04-saif-review.md` (Google Secure AI Framework Threat Model).
- `data/` & `config/` — `data/dirty_form_export.csv` (PII + Under-18 + prompt injection quarantine fixture) and `config/dataplex_dq_rules.yaml` (Dataplex AutoDQ rules `DQ-01..06`).
- `sql/` — BigQuery `europe-west2` 2-Dataset Medallion DDL & stored procedures (`01_staging_schema.sql`, `02_gold_schema_and_seed.sql`, `03_matching_and_simulation_procs.sql`).
- `src/` & `ui/` — Production ADK Multi-Agent hierarchy (`src/agent.py`, `src/server.py`), deterministic Python matcher (`src/matcher.py`), Regional `europe-west2` Model Armor guard (`src/model_armor_guard.py`), parameterized BigQuery repository (`src/bq_repository.py`), and Self-Service Portal (`src/portal_server.py`, `ui/portal_intake.html`).
- `tests/` & `scripts/` — Deterministic `pytest` suite (`tests/test_matcher.py`), 50-case Golden Eval Set (`tests/eval_golden_set.json`), evaluation runner (`scripts/run_eval_scorecard.py`), BigQuery/Dataplex provisioner (`scripts/provision_bq_and_dataplex.py`), and Custom Domain Global Load Balancer + Cloud IAP script (`scripts/setup_custom_domain_lb.sh`).
- `IT_HANDOVER_BUNDLE/` — Zero-Shadow-IT Enterprise promotion package (`IT_PROMOTION_RUNBOOK.md`, `FINOPS_SUMMARY.md`, `SECURITY_AND_EVAL_ATTESTATION.md`, `deploy_to_customer_ge.sh`).
