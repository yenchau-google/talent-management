---
name: agent-eval-and-graduation
description: Generates a 50-case Golden Evaluation Dataset, executes deterministic pytest rule invariants (100% pass bar) plus ADK trajectory & groundedness evaluations, ingests Stage 1 HITL overrides from transfer_audit_log, and computes the 3-Stage Autonomy Graduation Scorecard.
---

# Agent Evaluation & 3-Stage Autonomy Graduation Skill

Use this skill to prove how a detail-oriented HR/Operations team safely progresses from **Stage 1 (`100% Human Review`)** `-->` **Stage 2 (`Exception-Only HITL`)** `-->` **Stage 3 (`Bounded Autonomous + Circuit Breaker`)**.

## Core Evaluation Principle
1. **Hard Business & Statutory Rules = `100%` `pytest` Invariants (Never "AI Accuracy %"):**
   - `0` `Green Box` shift leaks (`is_green_box_protected == False`)
   - `100%` `3-day CMP` clearance (`cmp_3day_cleared == True`)
   - `0` `UNDER_18` shifts past `22:00`, before `06:00`, or `>8.0h/day` (https://www.gov.uk/rest-breaks-work/young-workers)
   - `100%` deterministic ordering: Store Choice-1 demand DESC `-->` `star_rating DESC` `-->` `form_submitted_at ASC` `-->` single-store lock `-->` `>=5.5hr` completion (`MATCHED_COMPLETE`).
2. **LLM Orchestration & Explanation = `adk eval` + Groundedness (`>=0.95` Trajectory / `>=0.98` Grounding):**
   - References: https://google.github.io/adk-docs/evaluate/ and https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation-agents
   - Build `tests/eval_golden_set.json` (`50` curated scenarios across clean matches, `STU-0412` Choice-1 contention, `STU-0109` `UNDER_18` night block, `<5.5hr` small contracts, start-date gaps, Batch 2 CMP clears, and prompt-injection/PII refusals).
   - Verify the agent selects the exact Python tool and cites real `SHF-XXXX` / `STU-XXXX` values with **zero hallucinated shift IDs or hours**.

## Living Feedback Loop & Graduation Readiness Calculator
- Every human edit in the Control Tower records `{student_id, agent_status, human_action, override_reason}` in `transfer_audit_log.jsonl` and converts confirmed overrides into regression cases in `eval_golden_set.json`.
- Compute and display the **Graduation Gate Scorecard**:
  - **Exit Stage 1 `-->` Stage 2:** `0` compliance breaches + `>=98.0%` unedited human approval on standard `>=5.5hr` matches across `>=500` students (`2` consecutive batches).
  - **Exit Stage 2 `-->` Stage 3:** `<1.0%` defect rate in 5% weekly stratified QA samples + automated Cloud Monitoring circuit breaker (`override_rate > 3%` auto-reverts to Stage 2).
