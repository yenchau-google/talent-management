# NEXT PLC — IT Handover & Gemini Enterprise Promotion Runbook (`IT_HANDOVER_BUNDLE`)

**Application:** NEXT Student Transfer Assistant (`next-student-transfer-agent`)  
**Target Customer Promotion Environment:** `NEXT_IT_TARGET_PROJECT_ID=next-ge-poc` | `NEXT_IT_TARGET_REGION=europe-west2`  
**Stage 1 Operating Mode:** `STAGE_1_100_PCT_HITL_REVIEW` (Staged `'Do Not Post'` NEP Batch Export — Zero Direct ERP Writes)

---

## 1. Zero Shadow IT Architecture Overview

This bundle enables **NEXT PLC Enterprise IT** (`roles/discoveryengine.admin` and `roles/run.admin`) to promote the verified Student Transfer Assistant into NEXT's corporate Google Cloud project (`next-ge-poc`) in under 15 minutes:

1. **Private Serverless Runtime (`europe-west2`):** Deployed to Cloud Run (`--no-allow-unauthenticated`, `--min-instances=0`, `--max-instances=2`, `--cpu-throttling`).
2. **Regional Model Armor (`europe-west2`):** Enforces `https://modelarmor.europe-west2.rep.googleapis.com` (`dataResidencyCompliant: true`) on every user prompt, tool output, and model response.
3. **Gemini Enterprise Registration:** Registers the private Cloud Run service as a governed conversational agent inside NEXT's Gemini Enterprise application.

---

## 2. Required IAM Roles for NEXT IT Administrator

Ensure the promoting principal holds:
- `roles/run.admin` (Deploy private Cloud Run service and grant `roles/run.invoker` to Discovery Engine service agent)
- `roles/modelarmor.admin` (Create regional `europe-west2` Model Armor template)
- `roles/discoveryengine.admin` & `roles/discoveryengine.agentspaceAdmin` (Register agent onto Gemini Enterprise)
- `roles/aiplatform.user` (Invoke `gemini-2.5-flash` via Vertex AI)

---

## 3. Step-by-Step Promotion Procedure

1. **Configure `.env` with NEXT IT Target Identifiers:**
   ```bash
   export NEXT_IT_TARGET_PROJECT_ID="next-ge-poc"
   export NEXT_IT_TARGET_REGION="europe-west2"
   export GEMINI_ENTERPRISE_LOCATION="eu"
   export GEMINI_ENTERPRISE_APP_ID="<YOUR_NEXT_GE_ENGINE_ID>"
   export MODEL_ARMOR_TEMPLATE_ID="next-student-transfer-armor"
   ```
2. **Run Pre-Flight Gate & Deterministic Unit Tests (Mandatory 100% Bar):**
   ```bash
   pytest -v tests/test_matcher.py
   python3 scripts/run_eval_scorecard.py
   ```
3. **Execute Automated Promotion Script:**
   ```bash
   chmod +x IT_HANDOVER_BUNDLE/deploy_to_customer_ge.sh
   ./IT_HANDOVER_BUNDLE/deploy_to_customer_ge.sh
   ```

---

## 4. Custom Domain, Shared Cloud DNS & Cloud IAP Architecture

- **Global External Application Load Balancer + Cloud IAP (`scripts/setup_custom_domain_lb.sh`):**
  - Fronts Cloud Run App 1 (`next-portal-intake-app`) with a Google-Managed SSL certificate, serverless NEG (`next-portal-neg`), and Cloud IAP (`roles/iap.httpsResourceAccessor`).
  - Supports cross-project Cloud DNS zones via `DNS_PROJECT_ID` and `DNS_ZONE_NAME` (`DNSSEC=off` recommended when using Google-Managed SSL certificates).
  - Automatically disables direct Cloud Run IAP (`--no-iap`) **before** re-binding `roles/run.invoker` to `service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com` so Load Balancer IAP never triggers `Error code 9` (`403`).

---

## 5. Rollback & Emergency Circuit Breaker

- **Stage 3 Circuit Breaker (`>3.0%` Override Rate):** Automatically downgrades autonomy back to Stage 2/Stage 1 if HR override rate exceeds `3.0%` in any 7-day rolling window.
- **Immediate Cloud Run Traffic Halt:**
  ```bash
  gcloud run services update-traffic next-student-transfer-agent \
    --region="${NEXT_IT_TARGET_REGION}" \
    --project="${NEXT_IT_TARGET_PROJECT_ID}" \
    --to-revisions=LATEST=0
  ```
