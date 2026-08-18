#!/usr/bin/env bash
# Bring up a kind cluster with the demo-app + Prometheus so real remediation
# has a real workload to act on. Requires: docker, kind, kubectl.
set -euo pipefail
CLUSTER=${CLUSTER:-intelliops}
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "→ Creating kind cluster '$CLUSTER'…"
kind create cluster --name "$CLUSTER" --config "$HERE/deploy/k8s/kind-config.yaml"

echo "→ Building + loading the demo-app image…"
# The shared image runs whatever $SERVICE_MODULE the container env sets (the
# demo-app deployment sets it to services.demo_app.app:app), so one plain build
# of deploy/Dockerfile is all that's needed.
docker build -t intelliops-demo-app:local -f "$HERE/deploy/Dockerfile" "$HERE"
kind load docker-image intelliops-demo-app:local --name "$CLUSTER"

echo "→ Applying manifests…"
kubectl create namespace intelliops-demo --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$HERE/deploy/k8s/demo-app/"
kubectl apply -f "$HERE/deploy/k8s/prometheus/"

echo "→ Waiting for rollouts…"
kubectl -n intelliops-demo rollout status deploy/demo-app --timeout=120s
kubectl -n intelliops-demo rollout status deploy/prometheus --timeout=120s

echo "✓ Cluster up."
echo "  Prometheus: http://localhost:30090"
echo "  kubeconfig: run 'kind get kubeconfig --name $CLUSTER > /tmp/intelliops.kubeconfig'"
echo "  Then start the stack with the k8s overlay (see deploy/k8s/README.md)."
