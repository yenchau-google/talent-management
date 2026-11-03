#!/usr/bin/env bash
# ==============================================================================
# NEXT PLC — Zero-Shadow-IT Promotion Script (Argolis Showcase -> Customer IT)
# ==============================================================================
# Usage:
#   export NEXT_IT_TARGET_PROJECT_ID="next-ge-poc"
#   export NEXT_IT_TARGET_REGION="europe-west2"
#   export GEMINI_ENTERPRISE_LOCATION="eu"
#   export GEMINI_ENTERPRISE_APP_ID="<YOUR_GE_ENGINE_ID>"
#   ./IT_HANDOVER_BUNDLE/deploy_to_customer_ge.sh
# ==============================================================================
set -euo pipefail

export CLOUDSDK_CONTEXT_AWARE_USE_CLIENT_CERTIFICATE=false

PROJECT_ID="${NEXT_IT_TARGET_PROJECT_ID:-${ARGOLIS_PROJECT_ID:-}}"
REGION="${NEXT_IT_TARGET_REGION:-${GCP_REGION:-europe-west2}}"
GE_LOCATION="${GEMINI_ENTERPRISE_LOCATION:-eu}"
GE_APP_ID="${GEMINI_ENTERPRISE_APP_ID:-}"
SERVICE_NAME="${CLOUD_RUN_SERVICE_NAME:-next-student-transfer-agent}"
ARMOR_TEMPLATE_ID="${MODEL_ARMOR_TEMPLATE_ID:-next-student-transfer-armor}"
GEMINI_MODEL="${GEMINI_MODEL_ID:-gemini-2.5-flash}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: Set NEXT_IT_TARGET_PROJECT_ID or ARGOLIS_PROJECT_ID before running." >&2
  exit 1
fi

echo "==> [1/4] Verifying Pre-Flight PII & Policy Gate on Gold Datasets..."
python3 .agents/skills/pii-and-policy-gate/scripts/scan_dataset.py data/students_gold.csv data/store_shifts_gold.csv

echo "==> [2/4] Ensuring Regional Model Armor Template (${ARMOR_TEMPLATE_ID}) in ${REGION}..."
gcloud config set api_endpoint_overrides/modelarmor "https://modelarmor.${REGION}.rep.googleapis.com/"
if ! gcloud model-armor templates describe "${ARMOR_TEMPLATE_ID}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud model-armor templates create "${ARMOR_TEMPLATE_ID}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --pi-and-jailbreak-filter-settings-enforcement=enabled \
    --pi-and-jailbreak-filter-settings-confidence-level=LOW_AND_ABOVE \
    --rai-settings-filters='[{"filterType":"HATE_SPEECH","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"HARASSMENT","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"DANGEROUS","confidenceLevel":"MEDIUM_AND_ABOVE"}]'
fi

echo "==> [3/4] Deploying Private Scale-to-Zero Cloud Run Service (${SERVICE_NAME}) in ${REGION}..."
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --no-allow-unauthenticated \
  --cpu-throttling \
  --min-instances=0 \
  --max-instances=2 \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GEMINI_MODEL_ID=${GEMINI_MODEL},ENABLE_MODEL_ARMOR=1,MODEL_ARMOR_LOCATION=${REGION},MODEL_ARMOR_TEMPLATE_ID=${ARMOR_TEMPLATE_ID}"

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
PROJECT_NUM="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "==> Granting roles/run.invoker to Discovery Engine Service Agent..."
gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:service-${PROJECT_NUM}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --quiet || true

echo "==> [4/4] Cloud Run Deployed at: ${SERVICE_URL}"
if [[ -n "${GE_APP_ID}" ]]; then
  echo "==> Registering Agent onto Gemini Enterprise App (${GE_APP_ID} in ${GE_LOCATION})..."
  ACCESS_TOKEN="$(gcloud auth print-access-token)"
  DISC_ENDPOINT="https://${GE_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${GE_LOCATION}/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents"
  AGENT_CARD_JSON="$(python3 -c "import json; print(json.dumps(json.dumps({'name':'next_student_transfer_assistant','description':'Governed UK Student Transfer Assistant for NEXT PLC (Zero PII, UK Under-18 Working-Time Hard Boundary, 3-Pass >=5.5hr Cascade, and Stage 1 HITL Control Tower).','url':'${SERVICE_URL}/a2a/app','version':'1.0.0','protocolVersion':'0.2.1','capabilities':{'streaming':False,'pushNotifications':False},'defaultInputModes':['text/plain'],'defaultOutputModes':['text/plain'],'skills':[{'id':'student_transfer_matching','name':'Student Transfer Matching & Exception Explanations','description':'Matches 1,050 tokenized students across 470 UK stores, explains placement provenance, runs Batch 2 3-day CMP re-matches, and simulates 4* 4.0hr contract overrides.','tags':['hr','retail','student-transfers','under-18-compliance']}]})))")"
  curl -sS -X POST "${DISC_ENDPOINT}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d "{
      \"displayName\": \"NEXT Student Transfer Assistant\",
      \"description\": \"Governed UK Student Transfer Assistant (Zero PII, UK Under-18 Working-Time Hard Boundary, 3-Pass >=5.5hr Cascade, and Stage 1 HITL Control Tower).\",
      \"a2aAgentDefinition\": {
        \"jsonAgentCard\": ${AGENT_CARD_JSON}
      },
      \"sharingConfig\": {
        \"scope\": \"ALL_USERS\"
      }
    }" || true
fi
echo "==> Promotion Complete."
