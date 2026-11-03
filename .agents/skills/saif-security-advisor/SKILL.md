---
name: saif-security-advisor
description: Threat-models a Google Cloud AI or agent solution design against Google's Secure AI Framework (SAIF — 15 risks, 24 controls, and Agent Extension), classifying every control as a Hard Boundary (B) vs Probabilistic Mitigation (M) with verified saif.google and docs.cloud.google.com citations.
---

# Google Secure AI Framework (`SAIF`) Security Advisor (Standalone Commercial Skill)

Use this skill to threat-model AI and agentic designs (`docs/02-solution-design/02-solution-design.md`) against `docs/01-scoping/01-scoping-requirements.md`, checking prompt injection, sensitive data disclosure, rogue actions, agent permissions, human control, and observability.

## Mandatory References
Read both bundled reference files first:
1. `.agents/skills/saif-security-advisor/references/saif_risk_map.md` (verbatim 15 SAIF risks -> 24 controls + Agent Extension)
2. `.agents/skills/saif-security-advisor/references/gcp_control_map.md` (SAIF control -> Google Cloud product -> Hard Boundary `B` vs Mitigation `M`)

## Non-Negotiable SAIF Rule ("Hard Boundary Rule")
Every **Critical** or **High** risk MUST be covered by **at least one deterministic Hard Boundary (`B`)** outside the LLM (such as upstream PII tokenization, deterministic Python statutory validators, IAM least privilege, or a mandatory HITL approval gate). **Model Armor (`M`) is mandatory in our Day-1 Base State, but a Critical/High risk covered *only* by a probabilistic classifier (`M`) without a Hard Boundary (`B`) must be flagged as an Open Finding!**

## Output Contract (`docs/02-solution-design/04-saif-review.md`, <=1,000 Words, Tables Over Prose)
1. **Executive SAIF Verdict & Traffic-Light Summary:** Overall posture + count of `Covered (B+M)` vs `Open Gaps`.
2. **SAIF Risk Triage (`15` Risks):** Applicable High/Critical risks (`Prompt Injection`, `Sensitive Data Disclosure`, `Insecure Model Output`, `Rogue Actions`, `Insecure Integrated Component`, `Denial of ML Service`, `Excessive Data Handling`) vs Out-of-Scope Model-Creator risks (since we consume managed Vertex AI `gemini-2.5-flash` without fine-tuning weights).
3. **Control Mapping Matrix (`Boundary [B]` vs `Mitigation [M]`):**
   Risk | SAIF Control (+ Agent Extension) | Implementation in Solution (incl. Regional `europe-west2` Model Armor Base State) | `B` or `M` | Status | Req IDs | Public URL.
4. **Agent Extension Deep-Check (`saif.google/secure-ai-framework/focus-on-agents`):**
   - **Agent Permissions:** Narrow typed read/compute tools only; zero free-form SQL or ERP write credentials.
   - **Agent User Control:** Stage 1 `100% HITL` approval before NEP `'Do Not Post'` batch export; Stage 3 circuit breaker.
   - **Agent Observability:** Structured `transfer_audit_log` + Model Armor sanitization telemetry.
