---
name: gcp-waf-advisor
description: Reviews a Google Cloud solution design against the official Google Cloud Well-Architected Framework (6 pillars + AI & ML perspective), tracing every finding to a requirement ID (FR-*/NFR-*) with verified public docs.cloud.google.com citations and a non-technical Executive Traffic-Light Scorecard.
---

# Google Cloud Well-Architected Framework (`WAF`) Advisor (Standalone Commercial Skill)

Use this skill when asked to run a Well-Architected review on `docs/02-solution-design/02-solution-design.md` against `docs/01-scoping/01-scoping-requirements.md`.

## Mandatory Reference
Read `.agents/skills/gcp-waf-advisor/references/waf_6_pillars_and_aiml.md` before writing any review. Never invent Google Cloud product limits, SLAs, prices, or launch stages.

## Workflow
1. **Ingest Inputs:** Read the requirements brief (`FR-*`, `NFR-*`) and the target solution design. Never review without requirement IDs.
2. **State Workload Profile:** Workload pattern (seasonal/bursty HR batch + conversational agent), failure domain (`europe-west2` regional serverless), RTO/RPO, and data classification (tokenized pseudonymous HR records + Under-18 compliance flags).
3. **Evaluate All 6 Pillars + AI & ML Perspective:**
   - Security, Privacy, and Compliance
   - Reliability
   - Cost Optimization (`FinOps`)
   - Operational Excellence (incl. continuous `adk eval` & graduation gates)
   - Performance Optimization
   - Sustainability (concise 1-row check on scale-to-zero / Flash right-sizing)
4. **Write Output (`docs/02-solution-design/03-waf-review.md`, <=1,000 Words, Tables Over Prose):**
   - **Executive Traffic-Light Scorecard:** Pillar | Status (`🟢 Optimal` / `🟡 Sub-Optimal` / `🔴 High-Risk`) | Business Plain-English Takeaway | Req IDs.
   - **Actionable Findings Table:** `WAF-NN` | Severity | Pillar | Design Gap | Concrete Google Cloud Remediation | Req IDs | Public Citation URL (`https://docs.cloud.google.com/...`).
   - **Key Architectural Trade-Offs (ADRs):** Record explicit trade-offs (e.g., scale-to-zero cold start vs `$0` off-season idle cost; deterministic Python matcher vs LLM explanation boundary).
   - Defer AI-specific adversarial risks (prompt injection, rogue agent actions) to `saif-security-advisor` (`-> see SAIF review`).
