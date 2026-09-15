#!/usr/bin/env bash
# Bring up the ENTIRE IntelliOps platform in a kind cluster via Helm — all 7
# services + Meridian + demo-app + Postgres + Redis + Prometheus + the React
# console — with the metrics-arc AI features LIVE (embedding + LLM), real k8s
# remediation, and per-metric health verification.
#
# Requires: docker, kind, kubectl, helm.
#
# The LLM key is read from your environment ($GROQ_API_KEY) and passed to Helm
# via --set-string. It is NEVER written to a file or committed. If unset, the
# stack still comes up but LLM explanations fall back to template.
#
# Usage:
#   GROQ_API_KEY=gsk_... ./scripts/kind-up-full.sh
#   CLUSTER=mycluster ./scripts/kind-up-full.sh        # custom cluster name
#   SAFE=1 ./scripts/kind-up-full.sh                   # default (safe) posture: no live overlay
set -euo pipefail

CLUSTER=${CLUSTER:-intelliops}
RELEASE=${RELEASE:-intelliops}
NAMESPACE=${NAMESPACE:-default}
HERE="$(cd "$(dirname "$0")/.." && pwd)"

# --- prerequisite checks ---------------------------------------------------
for tool in docker kind kubectl helm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "✗ '$tool' is required but not found on PATH." >&2
    echo "  Install it and retry. (helm: https://helm.sh/docs/intro/install/)" >&2
    exit 1
  fi
done
docker info >/dev/null 2>&1 || { echo "✗ Docker daemon is not running." >&2; exit 1; }

# --- 1. cluster ------------------------------------------------------------
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "→ kind cluster '$CLUSTER' already exists — reusing."
else
  echo "→ Creating kind cluster '$CLUSTER'…"
  kind create cluster --name "$CLUSTER" --config "$HERE/deploy/k8s/kind-config-full.yaml"
fi

# --- 2. images -------------------------------------------------------------
# base: the 11 lean services + demo-app + Meridian (all run $SERVICE_MODULE).
# full: rca + action (ml + k8s extras, CPU torch + baked embedding model).
# console: the React UI (nginx + same-origin backend proxy).
echo "→ Building images (base, full, console)… (full pulls torch CPU + bakes the model; first build is slow)"
docker build -t "intelliops:latest"          -f "$HERE/deploy/Dockerfile" --target base "$HERE"
docker build -t "intelliops:full"            -f "$HERE/deploy/Dockerfile" --target full "$HERE"
docker build -t "intelliops-console:latest"  -f "$HERE/deploy/Dockerfile.frontend"      "$HERE"

echo "→ Loading images into kind…"
kind load docker-image intelliops:latest         --name "$CLUSTER"
kind load docker-image intelliops:full           --name "$CLUSTER"
kind load docker-image intelliops-console:latest --name "$CLUSTER"

# --- 3. helm install -------------------------------------------------------
HELM_ARGS=(upgrade --install "$RELEASE" "$HERE/deploy/k8s/platform"
  --namespace "$NAMESPACE" --create-namespace
  --set image.tag=latest --set image.fullTag=full
  --set console.repository=intelliops-console)

if [ "${SAFE:-0}" = "1" ]; then
  echo "→ Installing SAFE posture (dry-run / off / template) — no live overlay."
else
  echo "→ Installing LIVE posture (embedding + k8s remediation + per-metric health)…"
  HELM_ARGS+=(-f "$HERE/deploy/k8s/platform/values-live.yaml")
  if [ -n "${GROQ_API_KEY:-}" ]; then
    echo "  LLM: wiring your \$GROQ_API_KEY via a Secret (never written to disk)."
    HELM_ARGS+=(--set-string "llm.apiKey=${GROQ_API_KEY}")
  else
    echo "  LLM: \$GROQ_API_KEY not set → explanations fall back to template."
  fi
fi

# Was this release already installed? A first install does not need the
# rollout restart below - Helm has just created every pod from the new images.
if helm status "$RELEASE" --namespace "$NAMESPACE" >/dev/null 2>&1; then
  FRESH_INSTALL=0
else
  FRESH_INSTALL=1
fi

helm "${HELM_ARGS[@]}"

# --- 3b. force the new images to actually run -------------------------------
# The image tag never changes (:latest / :full) and the chart uses
# imagePullPolicy: IfNotPresent, so Helm sees an identical pod spec and keeps
# the OLD pods. Without this, re-running the script after a code change
# rebuilds and loads images, reports success, and deploys nothing.
if [ "$FRESH_INSTALL" = "1" ]; then
  echo "→ Fresh install - Helm already created every pod from the new images;"
  echo "  skipping the rollout restart (it would only double the pod churn)."
  RESTARTED=-1
else
echo "→ Restarting workloads so the freshly-loaded images take effect…"
# Listed on one line on purpose: backslash continuations in a CRLF-checked-out
# script become an escaped CR instead of a line join.
RESTART_TARGETS="ingestion correlation rca action governance feedback read console demo-app meridian-gateway meridian-validation meridian-aggregation meridian-reporting"
RESTARTED=0
for d in $RESTART_TARGETS; do
  # Deliberately NOT silenced. The first version of this step hid its own output
  # behind >/dev/null 2>&1 || true and then failed invisibly - the script
  # reported success while the cluster kept running the PREVIOUS build, which is
  # the exact failure this step exists to prevent. A restart that cannot happen
  # has to be visible.
  if kubectl -n "$NAMESPACE" rollout restart "deploy/$d" 2>&1 | sed 's/^/    /'; then
    RESTARTED=$((RESTARTED + 1))
  else
    echo "    ! could not restart deploy/$d - it may still be running OLD code" >&2
  fi
done
echo "  restarted $RESTARTED workload(s)."
if [ "$RESTARTED" -eq 0 ]; then
  echo "  ! NOTHING was restarted - the cluster is probably still running the" >&2
  echo "    previous images. Re-run, or: kubectl rollout restart deploy/<name>" >&2
fi
fi

# --- 4. wait ---------------------------------------------------------------
echo "→ Waiting for rollouts…"
STALLED=""
for d in ingestion correlation rca action governance feedback read console demo-app prometheus \
         meridian-gateway meridian-validation meridian-aggregation meridian-reporting; do
  # NOT `|| true`. The previous version hid every timeout, so a stack that
  # never became ready still printed the success banner - the same
  # silent-success failure as the restart step above. On a cold cluster
  # postgres/redis/prometheus are pulled from Docker Hub, which regularly
  # pushes dependents past 180s, so this is a real outcome operators must see.
  if ! kubectl -n "$NAMESPACE" rollout status "deploy/$d" --timeout=300s; then
    STALLED="$STALLED $d"
  fi
done

# --- 5. done ---------------------------------------------------------------
echo ""
if [ -n "$STALLED" ]; then
  echo "✗ These workloads did NOT become ready:$STALLED" >&2
  echo "  The stack is NOT fully up. Inspect with:" >&2
  echo "    kubectl -n $NAMESPACE get pods" >&2
  echo "    kubectl -n $NAMESPACE describe deploy/<name>" >&2
  echo "  (A cold cluster pulls postgres/redis/prometheus from Docker Hub; a" >&2
  echo "   slow or rate-limited pull is the usual cause - re-running often works.)" >&2
  exit 1
fi
echo "✓ IntelliOps is up in kind cluster '$CLUSTER'."
echo "  Console (live UI):   http://localhost:30080"
echo "  Read service:        http://localhost:30007"
echo "  Meridian gateway:    http://localhost:30808  (inject faults here)"
echo "  Prometheus:          http://localhost:30090"
echo ""
echo "  Inject a fault via the demo-app / Meridian /admin/fault endpoint and watch"
echo "  detect → diagnose → approve → real pod remediation → per-metric verify in the console."
echo "  Tear down: kind delete cluster --name $CLUSTER"
