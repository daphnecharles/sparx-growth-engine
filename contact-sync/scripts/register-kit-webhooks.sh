#!/usr/bin/env bash
# Registers the four Kit (ConvertKit) webhooks that point at n8n.
# Run once, after n8n is live on HTTPS and workflows 1 and 4 are ACTIVE.
#
#   KIT_API_KEY=xxxx ./scripts/register-kit-webhooks.sh
#
# Reads N8N_DOMAIN and WEBHOOK_SHARED_SECRET from ../.env. The Kit API key is
# passed on the command line on purpose so it is never written to disk here.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${KIT_API_KEY:?Set KIT_API_KEY (Kit → Settings → Developer → API keys, v4 key)}"
set -a; source .env; set +a
: "${N8N_DOMAIN:?N8N_DOMAIN missing from .env}"
: "${WEBHOOK_SHARED_SECRET:?WEBHOOK_SHARED_SECRET missing from .env}"

base="https://${N8N_DOMAIN}/webhook"

register() {
  local event="$1" url="$2"
  echo "→ ${event}"
  curl -sS --fail-with-body -X POST "https://api.kit.com/v4/webhooks" \
    -H "X-Kit-Api-Key: ${KIT_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"target_url\": \"${url}\", \"event\": {\"name\": \"${event}\"}}"
  echo
}

register subscriber.subscriber_activate    "${base}/kit-subscriber-activate?token=${WEBHOOK_SHARED_SECRET}"
register subscriber.subscriber_unsubscribe "${base}/kit-subscriber-status?token=${WEBHOOK_SHARED_SECRET}&event=unsubscribe"
register subscriber.subscriber_bounce      "${base}/kit-subscriber-status?token=${WEBHOOK_SHARED_SECRET}&event=bounce"
register subscriber.subscriber_complain    "${base}/kit-subscriber-status?token=${WEBHOOK_SHARED_SECRET}&event=complain"

echo "Done. List them with:"
echo "  curl -sS https://api.kit.com/v4/webhooks -H \"X-Kit-Api-Key: \$KIT_API_KEY\""
