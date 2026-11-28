# Port map

Goal: all backend code in Rust, deployed free on ARM, one service at a time
(see [DECISIONS.md](DECISIONS.md)). `plan.md` is the `rust-anything analyze` output.

| Piece | Rust | Proof | State |
| --- | --- | --- | --- |
| Wire contracts, envelope, ids | `crates/contracts` | recorded streams round-trip; 600 fingerprint cases | done |
| Fingerprint (plugin unit) | `units/ingestion` | `rust-anything gate` passes, 600/600 | done |
| Online z-score and detection policy | `crates/detect` | 9,000 recorded steps, to 1e-12 (libm `pow` differs by platform) | done |
| Redis Streams bus | `crates/bus` | live stack | done |
| Storage (all tables) | `crates/store` | same tests on memory and Postgres | done |
| Correlation service | `crates/correlation` | 20,800 recorded engine ops; live | done except the `trained` detector |
| Service base: probes, metrics, auth, logging | `crates/service` | unit tests | done |
| Ingestion service | `crates/ingestion` | 93 recorded `/ingest` cases; live in the compose stack | done (compose switch only) |
| Feedback service | `crates/feedback` | recorded rules; live | done |
| Read service | `crates/read` | recorded projection; live | done |
| Governance service and AI runbook author | `crates/governance`, `crates/llm` | 3,015 fnmatch cases, API tests, scripted model; live incident approval and live draft | done |
| RCA service (rules, embeddings, explainer) | `crates/rca`, `crates/embed` | 400 recorded diagnoses; vectors match sentence-transformers; live | done |
| Action service: gates, Kubernetes, health, sandbox | `crates/action` | 2,500 recorded gate cases, 600 health cases, mock API server; live dry run | done; real-cluster test pending |
| demo_app, Meridian | | | open |
| Per-service switch (Helm), arm64 images | | | open (compose switch done) |
| Public demo mode, auth fix (#59, #60), Docs page rewrite | | | open |
| Free deployment | | | open |

Ingestion, correlation, feedback, read, governance, rca and action (all platform services) run on Rust when `deploy/docker-compose.rust.yml` is added.

## Working on it
- Oracles: `app-cases/golden/` (live traffic), `app-cases/detector.json`
  (`harness/record_detector.py`), `units/*/cases.jsonl`.
- Python baseline: `baselines/python.json`, produced by `harness/measure.py`.
- Checks: `cargo fmt --all --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test` in `rust/`.

## Next, in order
1. Real-cluster test of action, then demo_app and Meridian.
2. The `trained` (isolation forest) detector, before Python is removed.

## Limits of the golden recording
It proves message and response shapes only. It keeps the last 500 telemetry
entries and its windows depended on live timing, so it cannot verify the
correlation engine. For each stateful piece (correlation engine, RCA ranking,
remediation gates, feedback) record the Python class in-process with seeded
inputs and fakes, as `harness/record_detector.py` does.

## After cut-over
- Recurring incidents share one id (`appr-sit-<signature>`). The format is a
  stored key, so do not change it during the port.
