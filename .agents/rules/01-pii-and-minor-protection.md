---
trigger: always_on
description: Enforce zero raw PII (STU-XXXX / SM-XXX tokenization) and UK Under-18 Young Worker shift guardrails across all data, prompts, logs, and UI outputs.
---

# Rule 01 — PII Tokenization & UK Under-18 Protection (Non-Negotiable)

1. **Zero Raw PII in Datasets, Prompts, Logs, or UI:**
   - Never ingest, generate, log, or display student full names, personal/work email addresses, UK phone numbers (`+44...`, `07...`), dates of birth (`DOB`), home postcodes, or National Insurance (`NI`) numbers.
   - Represent all people strictly by pseudonymous tokens: `STU-XXXX` for students and `SM-XXX` for Store Managers.
   - Before processing any new CSV/JSON dataset, run `.agents/skills/pii-and-policy-gate/scripts/scan_dataset.py` and abort immediately if any PII column or regex match is detected.

2. **UK Young Worker (`UNDER_18`) Hard Boundary:**
   - Replace raw `DOB` upstream with a two-state `age_band` enum: `UNDER_18` or `ADULT_18_PLUS`.
   - Enforce UK Young Worker statutory limits in **deterministic Python** (never delegated to LLM judgement), citing https://www.gov.uk/rest-breaks-work/young-workers:
     - **Zero shifts ending after `22:00` or starting before `06:00`** (strictly block late-night retail closes and overnight stock-ins for `UNDER_18`).
     - **Maximum `8.0` hours per day** and **`40.0` hours per week**.
   - Any attempt to match an `UNDER_18` student to a shift violating these bounds must fail closed in code and raise a unit test failure in `pytest`.
