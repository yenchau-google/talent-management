---
name: ge-release-packager
description: Deploys the verified ADK + FastAPI agent to private Cloud Run (--no-allow-unauthenticated) with regional Model Armor (europe-west2) in the user's Argolis project (.env), registers it onto Argolis Gemini Enterprise, and generates an IT_HANDOVER_BUNDLE/ for zero-Shadow-IT customer IT promotion.
---

# Gemini Enterprise Release Packager (`Argolis Live Demo` + `Zero-Shadow-IT Handover`)

Use this skill in **Act 4** after `pytest`, `pii-and-policy-gate`, `gcp-waf-advisor`, `saif-security-advisor`, and `agent-eval-and-graduation` all pass.

## Environment Boundary Rule
- Read `ARGOLIS_PROJECT_ID`, `GOOGLE_CLOUD_REGION` (`europe-west2`), `MODEL_ARMOR_TEMPLATE_ID`, and `GEMINI_ENTERPRISE_APP_ID` strictly from `.env` (never hardcode project IDs).
- **Never** deploy directly to a customer GCP project (`NEXT_IT_TARGET_PROJECT_ID` is used strictly as documentation metadata inside `IT_HANDOVER_BUNDLE/`).

## Part A — Live Argolis Cloud Run + Regional Model Armor + Gemini Enterprise Registration
1. **Verify Regional Model Armor Template (`europe-west2`):**
   Ensure endpoint override is set before calling Model Armor CLI (https://docs.cloud.google.com/model-armor/data-residency):
   ```bash
   gcloud config set api_endpoint_overrides/modelarmor "https://modelarmor.europe-west2.rep.googleapis.com/"
   gcloud model-armor templates describe "${MODEL_ARMOR_TEMPLATE_ID}" --location="${MODEL_ARMOR_LOCATION}" --project="${ARGOLIS_PROJECT_ID}"
   ```
2. **Deploy Private Cloud Run Service (`europe-west2`, Scale-to-Zero, Least Privilege):**
   ```bash
   gcloud run deploy "${CLOUD_RUN_SERVICE_NAME}" \
     --source . \
     --project "${ARGOLIS_PROJECT_ID}" \
     --region "${GOOGLE_CLOUD_REGION}" \
     --no-allow-unauthenticated \
     --min-instances=0 \
     --max-instances=2 \
     --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${ARGOLIS_PROJECT_ID},GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION},GEMINI_MODEL_ID=${GEMINI_MODEL_ID},ENABLE_MODEL_ARMOR=1,MODEL_ARMOR_LOCATION=${MODEL_ARMOR_LOCATION},MODEL_ARMOR_TEMPLATE_ID=${MODEL_ARMOR_TEMPLATE_ID}"
   ```
3. **Grant Discovery Engine Invoker & Register on Argolis Gemini Enterprise:**
   - Grant `roles/run.invoker` on `${CLOUD_RUN_SERVICE_NAME}` to `service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com` (https://docs.cloud.google.com/gemini/enterprise/docs/manage-agents).
   - Expose OpenAPI (`${SERVICE_URL}/openapi.json`) and/or A2A Agent Card (`${SERVICE_URL}/a2a/app/.well-known/agent-card.json`) and register via `agents-cli register` / `agents-cli publish gemini-enterprise` (with REST `discoveryengine` curl fallback if `agents-cli` is not installed).

## Part B — Generate `IT_HANDOVER_BUNDLE/` (Kills Shadow IT Concerns)
Always generate `IT_HANDOVER_BUNDLE/` containing:
1. `IT_PROMOTION_RUNBOOK.md` — 2-minute promotion checklist for Enterprise IT (`roles/discoveryengine.admin` + Agent Designer admin approval gate: https://docs.cloud.google.com/gemini/enterprise/docs/agent-designer).
2. `SECURITY_AND_EVAL_ATTESTATION.md` — Combined `pii-and-policy-gate` status, `pytest` (`100%`), `adk eval` scorecard, WAF traffic-light summary, and SAIF Hard-Boundary (`B`) + Model Armor (`M`) attestation.
3. `deploy_to_customer_ge.sh` — Parameterized shell script reading `.env.example` variables (`NEXT_IT_TARGET_PROJECT_ID`, `NEXT_IT_TARGET_REGION`) for IT review.
