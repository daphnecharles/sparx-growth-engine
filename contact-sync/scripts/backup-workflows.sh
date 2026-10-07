#!/usr/bin/env bash
# Exports every n8n workflow as JSON into ./backups/<date>/ on the VM.
#   ./scripts/backup-workflows.sh
# Then copy that folder to the shared "Sparx Labs / Automation Backups" Drive
# folder (or commit it to the private company GitHub repo).
#
# Credentials are NOT exported (they contain live API keys). After a restore,
# re-enter them in n8n → Credentials; see README → "Backups".
set -euo pipefail
cd "$(dirname "$0")/.."

stamp="$(date +%Y-%m-%d_%H%M)"
dest="backups/${stamp}"
mkdir -p "$dest"

docker compose exec -T n8n sh -c 'rm -rf /tmp/wf-export && mkdir -p /tmp/wf-export && n8n export:workflow --all --separate --pretty --output=/tmp/wf-export/'
docker compose cp n8n:/tmp/wf-export/. "$dest/"
docker compose exec -T n8n rm -rf /tmp/wf-export

echo "Exported $(ls "$dest" | wc -l) workflow(s) to ${dest}"
