---
trigger: always_on
description: Enforce FinOps token discipline, Gemini 2.5 Flash routing, zero row-by-row LLM loops, and scale-to-zero Cloud Run defaults.
---

# Rule 02 — FinOps, Model Tiering & Token Quota Guardrails

1. **Default Runtime Model (`gemini-2.5-flash`):**
   - Use `gemini-2.5-flash` (parameterized via `GEMINI_MODEL_ID` in `.env`) for all runtime agent tool orchestration, exception explanations, and Gemini Enterprise conversational Q&A. Never default runtime batch endpoints to frontier `pro` models unless explicitly justified by an evaluation failure.

2. **Strict Ban on Row-by-Row LLM Loops (`>25` Rows):**
   - Never invoke `generate_content` or an LLM agent inside a `for` loop iterating over `1,000+` students or `470+` stores.
   - Execute bulk eligibility, store demand sorting, candidate ranking, and 3-pass `>=5.5hr` shift matching in **vectorized/in-memory Python (`O(N log N)`)**, calling Gemini **only** for:
     - Aggregated batch summaries and top-level exception briefings, or
     - Interactive per-query HR/SM explanations inside Gemini Enterprise / Control Tower.

3. **Compute & Token Budget Controls:**
   - Configure all Cloud Run deployments with `--min-instances=0 --max-instances=2 --cpu-throttling` so seasonal summer/Christmas transfer workloads incur **$0 idle compute cost** outside active HR windows.
   - Enforce `max_output_tokens <= 1024` on operational endpoints and document token quota caps aligned with Gemini Enterprise pooled developer quota (`$15/seat/month`) and Apigee `LLMTokenQuota` (https://docs.cloud.google.com/apigee/docs/api-platform/reference/policies/llm-token-quota-policy).
