---
name: workflow-scoper
description: Converts raw operational process notes into structured agent scoping artefacts — Functional & Non-Functional Requirements (FR-*/NFR-*), 3-Persona RACI Matrix, Zero-PII Tokenized Gold Schemas, 3-Stage Autonomy KPIs & Evaluation Exit Gates, Risk Register, and Policy Ambiguity Flags requiring human sign-off.
---

# Workflow Scoper Skill (`Agentify Any Manual Process`)

Use this skill whenever the user provides raw SOPs, Google Sheet/Form process notes, or operational plans and asks to scope an agentic workflow before writing code.

## Workflow
1. **Ingest Operational Notes:** Read the target input file (e.g., `inputs/next_student_transfer_raw_notes.md`) and identify:
   - Volume, frequency, and manual pain points ("As-Is" bottlenecks).
   - Personas involved (e.g., Applicants/Students, Store Managers, Central HR/Ops Specialists, Enterprise IT).
   - Hard deterministic rules vs. language/judgement tasks.
   - Unresolved margin comments or policy ambiguities that **must not** be guessed by an LLM.

2. **Generate `docs/01-scoping/01-scoping-requirements.md` (Concise, Table-Driven, <=1,100 Words):**
   Include these exact 6 sections:
   - **Section 1 — Executive Summary & MVP Boundary:** State which persona owns Phase 1 MVP (e.g., Central HR Specialists), what upstream/downstream systems are mocked vs. staged, and the **Zero Shadow IT** operating boundary.
   - **Section 2 — 3-Persona RACI & Phase Roadmap:** Table mapping Persona | Role in Phase 1 MVP | Phase 2/3 Evolution | Data Boundary (`STU-XXXX` / `SM-XXX` only).
   - **Section 3 — Traceable Requirements (`FR-01`..`FR-08`, `NFR-01`..`NFR-05`):**
     - Split **Deterministic Invariants (`100%` `pytest` pass required)** from **LLM Orchestration & Explanation (`adk eval` groundedness)**.
     - Always include `NFR-01` for **Regional Model Armor (`europe-west2`) Day-1 Base State** (https://docs.cloud.google.com/model-armor/overview) and `NFR-02` for **UK Under-18 Young Worker & Zero-PII Tokenization** (https://www.gov.uk/rest-breaks-work/young-workers).
   - **Section 4 — Zero-PII Gold Data Contracts:** Exact column schemas and types for `students_gold.csv` and `store_shifts_gold.csv`, explicitly banning raw names, emails, phones, DOBs, and NI numbers.
   - **Section 5 — Policy Ambiguities & Edge Cases Surfaced for Business Sign-Off:** Table listing every unresolved operational edge case found in the notes (e.g., start-date gaps, home-store shift release timing, `<5.5hr` small-contract opt-in vs SM shift extension) with recommended safe exception-queue routing (`DATE_GAP_REVIEW`, `SMALL_CONTRACT_EXCEPTION`, `SM_EXTEND_SHIFT_REVIEW`).
   - **Section 6 — 3-Stage Phased Launch, KPIs & Earned Autonomy Exit Gates:**
     - **Stage 1 (`100% HITL Co-Pilot`):** Human reviews 100% of matches; 1-click Override Reason builds the Golden Eval Set.
     - **Stage 2 (`Exception-Only HITL`):** Clean matches auto-stage after `>=500` placements across `2` batches hit `>=98%` unedited human approval and `0` policy breaches.
     - **Stage 3 (`Bounded Autonomous + Circuit Breaker`):** Continuous event-driven matching with `<1%` QA sample drift and auto-revert to Stage 2 if override rate `>3%`.
