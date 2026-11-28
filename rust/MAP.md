# Port map

Goal: all backend code in Rust, deployed free on ARM, one service at a time
(see [DECISIONS.md](DECISIONS.md)). `plan.md` is the `rust-anything analyze` output.

| Piece | Rust | Proof | State |
| --- | --- | --- | --- |
| Wire contracts, envelope, ids | `crates/contracts` | recorded streams round-trip; 600 fingerprint cases | done |
| Fingerprint (plugin unit) | `units/ingestion` | `rust-anything gate` passes, 600/600 | done |
| Online z-score and detection policy | `crates/detect` | 9,000 recorded steps, within 1 ulp (libm `pow` differs by platform) | done |
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

## Next, in order
1. Bus crate, then a Rust ingestion service behind a per-service image switch,
   then `measure.py` against it. Keep each Python consumer's group name, a stable
   consumer name, at-most-once by default and the same approximate MAXLEN.
2. Before ingestion: record what `IngestBatch.model_validate` accepts for odd
   timestamps (int, float, numeric string, naive, date only), values (`"1.5"`,
   `"nan"`, booleans) and non-string labels. `/ingest` leniency is unverified.

## Limits of the golden recording
It proves message and response shapes only. It keeps the last 500 telemetry
entries and its windows depended on live timing, so it cannot verify the
correlation engine. For each stateful piece (correlation engine, RCA ranking,
remediation gates, feedback) record the Python class in-process with seeded
inputs and fakes, as `harness/record_detector.py` does.

## After cut-over
- Recurring incidents share one id (`appr-sit-<signature>`). The format is a
  stored key, so do not change it during the port.
