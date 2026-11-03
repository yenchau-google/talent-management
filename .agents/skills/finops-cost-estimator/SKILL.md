---
name: finops-cost-estimator
description: Audits agent source code for token-loop anti-patterns, verifies Cloud Run scale-to-zero settings, and produces a plain-English FinOps Cost & Quota Scorecard against Gemini Enterprise's pooled $15/seat/month developer allocation.
---

# FinOps & Token Cost Estimator Skill

Use this skill after authoring the solution design or agent runtime code to verify compliance with **Rule 02** and produce a clear cost breakdown for business and IT stakeholders.

## Audit Checklist
1. **Static Code Loop Check:**
   - Grep all `.py` files for `generate_content`, `Runner`, or `Client` calls inside `for` / `while` loops over student or shift arrays (`>25` iterations). Flag any match as a **CRITICAL FinOps Blocker**.
2. **Model & Token Parameter Check:**
   - Verify runtime defaults to `gemini-2.5-flash` with `max_output_tokens <= 1024`.
3. **Serverless Scale-to-Zero Check:**
   - Verify Cloud Run deploy flags include `--min-instances=0 --max-instances=2 --cpu-throttling`.
4. **Output FinOps Table (Append to `docs/02-solution-design/03-waf-review.md` or `IT_HANDOVER_BUNDLE/FINOPS_SUMMARY.md`):**
   - Compare **Anti-Pattern (`Row-by-Row LLM`: `1,050 students x 3 choices = 3,150 LLM calls/run`)** vs **Hybrid Architecture (`Python Matcher = $0.00` + `Gemini 2.5 Flash Summary/Q&A = <$0.05/batch`, `<$5/month` total)**.
   - Show coverage under Gemini Enterprise's pooled developer quota (**`$15/seat/month`**) and enterprise token governance via Apigee `LLMTokenQuota` (https://docs.cloud.google.com/apigee/docs/api-platform/reference/policies/llm-token-quota-policy).
