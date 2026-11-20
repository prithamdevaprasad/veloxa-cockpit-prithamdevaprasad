#!/usr/bin/env bash
# Pause, resume and inspect the hosted Veloxa staging environment WITHOUT deleting any data.
#   bash deploy/veloxa-hosting.sh status  <project>
#   bash deploy/veloxa-hosting.sh pause   <project>
#   bash deploy/veloxa-hosting.sh resume  <project> [site-url]
#
# pause : the worker stops taking new work and scales to zero, the API scales to zero, then the Cloud SQL instance
#         is stopped (activation policy NEVER). The database disk, backups, the evidence bucket and every secret are
#         kept. While paused the site cannot answer (the database is off); sign-in and data come back on resume.
# resume: Cloud SQL is started and the script waits until it is RUNNABLE; the worker is restarted always-on and must
#         log a completed intake pass (proof it reached the database); the API is warmed; the site health is checked.
#         Background work: the hosted worker's intake loop keeps its state in the database (submission states),
#         so anything uploaded before the pause is picked up on the first pass. No DBOS workflows run in this
#         deployment (no workflow database is configured), so there is no DBOS journal to recover; the script says so.
# status: what is running and what it costs per day now (list-price estimates, asia-south1; check the billing page).
set -euo pipefail
cmd="${1:?status|pause|resume}"; P="${2:?project id}"; SITE="${3:-https://veloxa-demo.veloxa-cockpit-frontend.workers.dev}"
R=asia-south1; SQL=veloxa-staging
G="gcloud --project $P"

sql_state() { $G sql instances describe $SQL --format="value(state,settings.activationPolicy)" 2>/dev/null || echo "MISSING -"; }
svc() { $G run services describe "$1" --region $R --format="value(spec.template.metadata.annotations['autoscaling.knative.dev/minScale'],spec.template.metadata.annotations['run.googleapis.com/cpu-throttling'])" 2>/dev/null || echo "missing -"; }

status() {
  read -r sstate spol <<<"$(sql_state)"
  read -r amin _ <<<"$(svc veloxa-api)"; read -r wmin wthr <<<"$(svc veloxa-worker)"
  amin=${amin:-0}; wmin=${wmin:-0}
  echo "Cloud SQL ($SQL): $sstate (activation $spol)"
  echo "API:    min instances ${amin}"
  echo "Worker: min instances ${wmin}, CPU throttled: ${wthr:-true}"
  echo "Evidence bucket: $(gcloud storage du -s "gs://$P-evidence" 2>/dev/null | awk '{print $1}') bytes"
  # Per-day list-price estimates (USD): db-g1-small running 1.20, stopped disk+backups 0.17; always-on worker 1.75;
  # one warm API instance 0.50; secrets, registry, bucket 0.05.
  sqld=$( [ "$spol" = "ALWAYS" ] && echo 1.20 || echo 0.17 )
  wd=$( [ "$wmin" != "0" ] && echo 1.75 || echo 0 ); ad=$( [ "$amin" != "0" ] && echo 0.50 || echo 0 )
  awk -v a="$sqld" -v b="$wd" -v c="$ad" 'BEGIN { t=a+b+c+0.05; printf "Estimated now: about US$%.2f a day (about Rs %.0f a day, at Rs 85 per USD)\n", t, t*85 }'
}

case "$cmd" in
  status) status ;;
  pause)
    echo "1/3 Worker: stop always-on (scale to zero; it finishes the pass in progress before the instance stops)"
    $G run services update veloxa-worker --region $R --min-instances=0 --cpu-throttling -q >/dev/null
    echo "2/3 API: scale to zero"
    $G run services update veloxa-api --region $R --min-instances=0 -q >/dev/null
    echo "3/3 Cloud SQL: stop (data, backups and disk are kept)"
    $G sql instances patch $SQL --activation-policy=NEVER -q >/dev/null
    status ;;
  resume)
    echo "1/4 Cloud SQL: start"
    $G sql instances patch $SQL --activation-policy=ALWAYS -q >/dev/null
    for i in $(seq 60); do s=$(sql_state | awk '{print $1}'); [ "$s" = "RUNNABLE" ] && break; sleep 10; done
    [ "$(sql_state | awk '{print $1}')" = "RUNNABLE" ] || { echo "Cloud SQL did not become ready"; exit 1; }
    echo "    database is RUNNABLE"
    echo "2/4 Worker: restart always-on and wait for a completed intake pass"
    since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    $G run services update veloxa-worker --region $R --min-instances=1 --no-cpu-throttling -q >/dev/null
    ok=""
    for i in $(seq 30); do
      if $G logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"veloxa-worker\" AND textPayload:\"intake:\" AND timestamp>=\"$since\"" --limit=1 --format="value(textPayload)" 2>/dev/null | grep -q "intake:"; then ok=1; break; fi
      sleep 10
    done
    [ -n "$ok" ] && echo "    worker reached the database and completed an intake pass" || { echo "    worker has not logged an intake pass yet; check its logs"; exit 1; }
    echo "    DBOS: no workflow database is configured in this deployment, so there is no DBOS journal to recover"
    echo "3/4 API: keep one instance warm"
    $G run services update veloxa-api --region $R --min-instances=1 -q >/dev/null
    echo "4/4 Site: health check"
    code=$(curl -s -o /dev/null -w "%{http_code}" "$SITE/health"); echo "    $SITE/health -> $code"
    [ "$code" = "200" ] || exit 1
    status ;;
  *) echo "usage: $0 status|pause|resume <project> [site-url]"; exit 2 ;;
esac
