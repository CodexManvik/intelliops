# Port map

Goal: all backend code in Rust, deployed free on ARM, one service at a time
(see [DECISIONS.md](DECISIONS.md)). `plan.md` is the `rust-anything analyze` output.

| Piece | Rust | Proof | State |
| --- | --- | --- | --- |
| Wire contracts, envelope, ids | `crates/contracts` | recorded streams round-trip; 600 fingerprint cases | done |
| Fingerprint (plugin unit) | `units/ingestion` | `rust-anything gate` passes, 600/600 | done |
| Online z-score and detection policy | `crates/detect` | bit-exact over 9,000 recorded steps | done |
| Redis Streams bus | | | next |
| Ingestion service (incl. `/ingest` leniency cases) | | | open |
| Correlation service (windowing engine, robust and trained detectors) | | | open |
| Read, feedback | | | open |
| Governance (RBAC fnmatch, stores, migrations, runbook author) | | | open |
| RCA (rules, embeddings, LLM explainer) | | | open |
| Action (kube-rs remediation, health, sandbox) | | | open |
| demo_app, Meridian | | | open |
| Per-service Python/Rust image switch, arm64 images | | | open |
| Public demo mode, auth fix (#59, #60), Docs page rewrite | | | open |
| Free deployment | | | open |

No service runs on Rust yet.

## Working on it
- Oracles: `app-cases/golden/` (live traffic), `app-cases/detector.json`
  (`harness/record_detector.py`), `units/*/cases.jsonl`.
- Python baseline: `baselines/python.json`, produced by `harness/measure.py`.
- Checks: `cargo fmt --all --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test` in `rust/`.
