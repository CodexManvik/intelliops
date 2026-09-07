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

helm "${HELM_ARGS[@]}"

# --- 4. wait ---------------------------------------------------------------
echo "→ Waiting for rollouts…"
for d in ingestion correlation rca action governance feedback read console demo-app prometheus \
         meridian-gateway meridian-validation meridian-aggregation meridian-reporting; do
  kubectl -n "$NAMESPACE" rollout status "deploy/$d" --timeout=180s || true
done

# --- 5. done ---------------------------------------------------------------
echo ""
echo "✓ IntelliOps is up in kind cluster '$CLUSTER'."
echo "  Console (live UI):   http://localhost:30080"
echo "  Read service:        http://localhost:30007"
echo "  Prometheus/Meridian: in-cluster (add a NodePort to inspect directly)."
echo ""
echo "  Inject a fault via the demo-app / Meridian /admin/fault endpoint and watch"
echo "  detect → diagnose → approve → real pod remediation → per-metric verify in the console."
echo "  Tear down: kind delete cluster --name $CLUSTER"
