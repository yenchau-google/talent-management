---
trigger: always_on
description: Mandate deterministic Python + pytest for all matching math, regional Model Armor (europe-west2) in the Day-1 Base State, parameterized .env project IDs, and HITL staging.
---

# Rule 03 — Deterministic Math, Model Armor Base State & HITL Governance

1. **100% Deterministic Python for Operational Rules (`pytest` Verified):**
   - All business invariants — `3*`/`4*` permanent eligibility, Choice-1 store demand ranking, `star_rating DESC` + `form_submitted_at ASC` tie-breaking, 3-pass `Choice 1 -> Choice 2 -> Choice 3` cascade, unique-availability-first pass, single-store shift lock, `>=5.5hr` contract completion, `is_green_box_protected == False`, and `cmp_3day_cleared == True` — must execute in pure deterministic Python covered by `pytest`. Never ask an LLM to calculate shift hours or rank candidates.

2. **Regional Model Armor (`europe-west2`) in the Day-1 Base State:**
   - Every ADK agent and HTTP chat/tool boundary must wire a `ModelArmorGuard` (`before_model_callback`, `after_tool_callback`, `after_model_callback`).
   - When `ENABLE_MODEL_ARMOR=1` with Google Cloud credentials, call the regional London endpoint `https://modelarmor.europe-west2.rep.googleapis.com` (`sanitizeUserPrompt` / `sanitizeModelResponse`, https://docs.cloud.google.com/model-armor/overview and https://docs.cloud.google.com/model-armor/data-residency).
   - Provide a deterministic local fallback inspector when running offline in laptop workshops (`ENABLE_MODEL_ARMOR=local`) so prompt-injection and UK PII pastes still fail closed during local testing.

3. **Human-in-the-Loop (`HITL`) Staging & Zero Shadow IT:**
   - Phase 1 (`Stage 1: 100% Review`) never mutates downstream HR/ERP systems (`NPS` / `NEP` / `Oracle`) directly. Output approved placements solely to staged `'Do Not Post'` batch payloads and log every human override + reason to `transfer_audit_log` to feed the Golden Eval Set.
   - Read `ARGOLIS_PROJECT_ID` and `GEMINI_ENTERPRISE_APP_ID` strictly from environment variables (`.env`), never hardcoded in source files. Deploy Cloud Run with `--no-allow-unauthenticated`.
