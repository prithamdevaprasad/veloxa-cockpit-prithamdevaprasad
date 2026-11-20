#!/usr/bin/env bash
# Delete the hosted Veloxa staging environment so charges stop. Irreversible: the database and the evidence bucket
# are deleted with everything in them. Asks for confirmation; nothing is deleted without typing the project id.
# Usage: bash deploy/teardown.sh <gcp-project-id> [cloudflare-worker-name]
set -euo pipefail
P="${1:?project id}"; W="${2:-veloxa-demo}"
G="gcloud --project $P"
read -r -p "Type the project id ($P) to delete Veloxa staging in it: " ok
[ "$ok" = "$P" ] || { echo "not confirmed; nothing deleted"; exit 1; }
$G run services delete veloxa-api --region asia-south1 -q || true
$G run services delete veloxa-worker --region asia-south1 -q || true
$G sql instances delete veloxa-staging -q || true
gcloud storage rm -r "gs://$P-evidence" -q || true
for s in $($G secrets list --filter="name~veloxa-" --format="value(name)"); do $G secrets delete "$s" -q || true; done
$G artifacts repositories delete veloxa --location asia-south1 -q || true
for sa in veloxa-api veloxa-worker; do $G iam service-accounts delete "$sa@$P.iam.gserviceaccount.com" -q || true; done
(cd "$(dirname "$0")/../frontend" && npx --yes wrangler@4 delete --name "$W" --force) || true
echo "Deleted. To stop all billing on the project as well: gcloud billing projects unlink $P"
