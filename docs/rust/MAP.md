# Port map

The port is complete: the repository root is the Rust workspace and Python lives
on `python-legacy`. Paths below are from before that move (`crates/`, `testdata/`).

Goal: all backend code in Rust, deployed free on ARM, one service at a time
(see [DECISIONS.md](DECISIONS.md)). `plan.md` is the `rust-anything analyze` output.

| Piece | Rust | Proof | State |
| --- | --- | --- | --- |
| Wire contracts, envelope, ids | `crates/contracts` | recorded streams round-trip; 600 fingerprint cases | done |
| Fingerprint (plugin unit) | `units/ingestion` | `rust-anything gate` passes, 600/600 | done |
| Online z-score and detection policy | `crates/detect` | 9,000 recorded steps, to 1e-12 (libm `pow` differs by platform) | done |
| Redis Streams bus | `crates/bus` | live stack | done |
| Storage (all tables) | `crates/store` | same tests on memory and Postgres | done |
| Service base: probes, metrics, auth, logging | `crates/service` | unit tests | done |
| Ingestion service | `crates/ingestion` | 93 recorded `/ingest` cases; live in the compose stack | done (compose switch only) |
| Correlation service, all three detectors | `crates/correlation` | 20,800 engine ops; 5 benchmark streams; live | done |
| demo_app and Meridian | `crates/sim` | recorded faults and jitter; live | done |
| Feedback service | `crates/feedback` | recorded rules; live | done |
| Read service | `crates/read` | recorded projection; live | done |
| Governance service and AI runbook author | `crates/governance`, `crates/llm` | 3,015 fnmatch cases, API tests, scripted model; live incident approval and live draft | done |
| RCA service (rules, embeddings, explainer) | `crates/rca`, `crates/embed` | 400 recorded diagnoses; vectors match sentence-transformers; live | done |
| Action service: gates, Kubernetes, health, sandbox | `crates/action` | 2,500 recorded gate cases, 600 health cases, mock API server; live dry run; `tests/live_cluster.rs` on kind | done |

Ingestion, correlation, feedback, read, governance, rca and action and the demo apps run on Rust when `deploy/docker-compose.rust.yml` is added.

## Working on it
- Oracles: `app-cases/golden/` (live traffic), `app-cases/detector.json`
  (`harness/record_detector.py`), `units/*/cases.jsonl`.
- Python baseline: `baselines/python.json`, produced by `harness/measure.py`.
- Checks: `cargo fmt --all --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test` in `rust/`.

## Remaining
- Public demo mode, closing the caller-supplied identity gap (issues #59, #60).
- Rewrite the console's Docs page, which advertises a product that does not exist.
- Free deployment (needs the owner's cloud account).

