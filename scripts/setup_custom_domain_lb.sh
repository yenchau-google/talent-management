#!/usr/bin/env bash
# ==============================================================================
# NEXT PLC — Custom Domain Global Load Balancer, Google-Managed SSL & Cloud IAP
# ==============================================================================
# Provisions an External Application Load Balancer in front of Cloud Run App 1
# (`next-portal-intake-app`) with a Google-Managed SSL certificate, Cloud DNS A
# record registration, and Cloud Identity-Aware Proxy (IAP).
# ==============================================================================
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT in .env or environment}"
REGION="${GOOGLE_CLOUD_LOCATION:-europe-west2}"
DNS_PROJECT_ID="${DNS_PROJECT_ID:-${PROJECT_ID}}"
DNS_ZONE_NAME="${DNS_ZONE_NAME:-next-retail-zone}"
DOMAIN_NAME="${PORTAL_CUSTOM_DOMAIN:-next-portal.next-retail.example.com}"
SERVICE_NAME="${PORTAL_SERVICE_NAME:-next-portal-intake-app}"
IAP_ADMIN_PRINCIPAL="${IAP_ADMIN_PRINCIPAL:-hr-admin@next-retail.example.com}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "=== 1. Enabling Compute, Certificate Manager & IAP APIs in ${PROJECT_ID} ==="
gcloud services enable compute.googleapis.com certificatemanager.googleapis.com iap.googleapis.com \
  --project="${PROJECT_ID}"

echo "=== 2. Reserving Global Static IP (next-portal-lb-ip) ==="
gcloud compute addresses describe next-portal-lb-ip --global --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute addresses create next-portal-lb-ip \
    --network-tier=PREMIUM \
    --ip-version=IPV4 \
    --global \
    --project="${PROJECT_ID}"

LB_IP=$(gcloud compute addresses describe next-portal-lb-ip --global --project="${PROJECT_ID}" --format="value(address)")
echo "Reserved Load Balancer IP: ${LB_IP}"

echo "=== 3. Registering DNS A Record (${DOMAIN_NAME}. -> ${LB_IP}) in ${DNS_PROJECT_ID}/${DNS_ZONE_NAME} ==="
EXISTING_IP=$(gcloud dns record-sets list \
  --project="${DNS_PROJECT_ID}" \
  --zone="${DNS_ZONE_NAME}" \
  --name="${DOMAIN_NAME}." \
  --type="A" \
  --format="value(rrdatas[0])" 2>/dev/null || true)

if [[ -z "${EXISTING_IP}" ]]; then
  gcloud dns record-sets create "${DOMAIN_NAME}." \
    --project="${DNS_PROJECT_ID}" \
    --zone="${DNS_ZONE_NAME}" \
    --type="A" \
    --ttl=300 \
    --rrdatas="${LB_IP}"
elif [[ "${EXISTING_IP}" != "${LB_IP}" ]]; then
  gcloud dns record-sets update "${DOMAIN_NAME}." \
    --project="${DNS_PROJECT_ID}" \
    --zone="${DNS_ZONE_NAME}" \
    --type="A" \
    --ttl=300 \
    --rrdatas="${LB_IP}"
else
  echo "DNS A record ${DOMAIN_NAME}. already points to ${LB_IP}"
fi

echo "=== 4. Creating Serverless NEG (next-portal-neg) for ${SERVICE_NAME} in ${REGION} ==="
gcloud compute network-endpoint-groups describe next-portal-neg --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute network-endpoint-groups create next-portal-neg \
    --region="${REGION}" \
    --network-endpoint-type=serverless \
    --cloud-run-service="${SERVICE_NAME}" \
    --project="${PROJECT_ID}"

echo "=== 5. Creating Backend Service (next-portal-backend) & Attaching NEG ==="
gcloud compute backend-services describe next-portal-backend --global --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute backend-services create next-portal-backend \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --protocol=HTTPS \
    --port-name=http \
    --global \
    --project="${PROJECT_ID}"

BACKEND_HAS_NEG=$(gcloud compute backend-services describe next-portal-backend --global --project="${PROJECT_ID}" --format="value(backends)" || true)
if [[ -z "${BACKEND_HAS_NEG}" ]]; then
  gcloud compute backend-services add-backend next-portal-backend \
    --global \
    --network-endpoint-group=next-portal-neg \
    --network-endpoint-group-region="${REGION}" \
    --project="${PROJECT_ID}"
fi

echo "=== 6. Configuring IAP OAuth Brand/Client & Enabling Cloud IAP on Backend Service ==="
BRAND_NAME=$(gcloud iap oauth-brands list --project="${PROJECT_ID}" --format="value(name)" 2>/dev/null | head -n 1 || true)
if [[ -z "${BRAND_NAME}" ]]; then
  BRAND_NAME=$(gcloud iap oauth-brands create \
    --application_title="NEXT Student Transfer Portal" \
    --support_email="${IAP_ADMIN_PRINCIPAL}" \
    --project="${PROJECT_ID}" \
    --format="value(name)")
fi

CLIENT_NAME=$(gcloud iap oauth-clients list "${BRAND_NAME}" --project="${PROJECT_ID}" --format="value(name)" 2>/dev/null | head -n 1 || true)
if [[ -z "${CLIENT_NAME}" ]]; then
  CLIENT_NAME=$(gcloud iap oauth-clients create "${BRAND_NAME}" \
    --display_name="next-portal-backend-iap" \
    --project="${PROJECT_ID}" \
    --format="value(name)")
fi
OAUTH_CLIENT_ID="${CLIENT_NAME##*/}"
OAUTH_CLIENT_SECRET=$(gcloud iap oauth-clients describe "${CLIENT_NAME}" --project="${PROJECT_ID}" --format="value(secret)")

gcloud compute backend-services update next-portal-backend \
  --global \
  --protocol=HTTPS \
  --port-name=http \
  --iap=enabled,oauth2-client-id="${OAUTH_CLIENT_ID}",oauth2-client-secret="${OAUTH_CLIENT_SECRET}" \
  --enable-logging \
  --logging-sample-rate=1.0 \
  --project="${PROJECT_ID}"

# 1. Disable Cloud Run direct IAP FIRST (because --no-iap removes the IAP service agent from roles/run.invoker)
gcloud beta run services update "${SERVICE_NAME}" \
  --region="${REGION}" \
  --no-iap \
  --project="${PROJECT_ID}"

# 2. Re-grant roles/run.invoker to the IAP service agent AFTER --no-iap so Load Balancer IAP can invoke Cloud Run (fixes IAP Error Code 9 / 403)
gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
  --region="${REGION}" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --project="${PROJECT_ID}" >/dev/null

gcloud iap web add-iam-policy-binding \
  --resource-type=backend-services \
  --service=next-portal-backend \
  --member="user:${IAP_ADMIN_PRINCIPAL}" \
  --role="roles/iap.httpsResourceAccessor" \
  --project="${PROJECT_ID}" >/dev/null

echo "=== 7. Creating URL Map, Google-Managed SSL Certificate & HTTPS Proxy ==="
gcloud compute url-maps describe next-portal-url-map --global --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute url-maps create next-portal-url-map \
    --default-service=next-portal-backend \
    --global \
    --project="${PROJECT_ID}"

gcloud compute ssl-certificates describe next-portal-ssl-cert --global --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute ssl-certificates create next-portal-ssl-cert \
    --domains="${DOMAIN_NAME}" \
    --global \
    --project="${PROJECT_ID}"

gcloud compute target-https-proxies describe next-portal-https-proxy --global --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute target-https-proxies create next-portal-https-proxy \
    --ssl-certificates=next-portal-ssl-cert \
    --url-map=next-portal-url-map \
    --global \
    --project="${PROJECT_ID}"

echo "=== 8. Creating Global HTTPS Forwarding Rule (next-portal-https-fw-rule) ==="
gcloud compute forwarding-rules describe next-portal-https-fw-rule --global --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud compute forwarding-rules create next-portal-https-fw-rule \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --network-tier=PREMIUM \
    --address=next-portal-lb-ip \
    --global \
    --target-https-proxy=next-portal-https-proxy \
    --ports=443 \
    --project="${PROJECT_ID}"

echo "=== Summary ==="
echo "Custom Domain: https://${DOMAIN_NAME}/ui"
echo "Load Balancer IP: ${LB_IP}"
gcloud compute ssl-certificates describe next-portal-ssl-cert --global --project="${PROJECT_ID}" --format="yaml(name,managed.status,managed.domainStatus)"
