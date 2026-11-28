# Port decisions

Source of truth for the Rust rewrite. One section per decision, numbered in
order and never renumbered; a changed decision gets a new section that
supersedes the old one. The owner delegated design decisions on 2026-11-28
("do everything autonomously ... really really impressive").

## ADR-001: Scope is all backend code
Status: accepted (2026-11-28)
Context: The owner asked for the rebuild to be complete.
Decision: Port every platform service, `common/`, Meridian and demo_app to Rust.
The React consoles stay. Python stays in the tree as reference until each
service's cut-over is proven.
Consequences: No Python runtime in the final deployment.

## ADR-002: Strangler port, one whole service at a time
Status: accepted (2026-11-28)
Context: Behaviour lives in consumer threads, Postgres, the Kubernetes API and
Prometheus polling, which a request/response front door cannot cover.
Decision: Pure logic goes through the rust-anything unit flow (record, diff,
gate). Service shells cut over whole, checked against the bus-level golden
recording in `app-cases/golden/`. Each service has an image selector in compose
and Helm, so one value flips it back to Python.
Consequences: Every milestone leaves a deployable mixed stack.

## ADR-003: Equivalence bar
Status: accepted (2026-11-28)
Decision:
- Wire JSON is parse-compatible in both directions, not byte-identical.
- Fingerprints, signatures and `sit-` / `appr-` / `ai-` id formats are
  byte-identical: they are keys in Postgres and in the console.
- `/ingest` accepts the same loose inputs Pydantic does (datetime forms,
  numeric strings), covered by recorded cases.
- The online z-score matches River exactly: same update order, same float results.
- Policy globs follow Python `fnmatch`, covered by recorded cases.
- IsolationForest and embeddings cannot match bit for bit. The bar is the
  `services/correlation/benchmark` scenarios and the same accept/reject outcome
  at the selector's 0.45 threshold.

## ADR-004: Drop Kafka, keep the sandbox rehearsal
Status: accepted (2026-11-28)
Context: Kafka has never been deployed. "Rehearse before applying" is a strong
part of the demo.
Decision: Redis Streams only. The sandbox is ported last, behind its flag.
Consequences: The sandbox needs rights to create namespaces, so it stays off in
the public deployment.

## ADR-005: Database schema is unchanged
Status: accepted (2026-11-28)
Decision: Seed the Rust migrations from `alembic upgrade head --sql` and compare
the resulting schemas.
Consequences: Python and Rust services can share one database during the port.

## ADR-006: Free ARM hosting is a hard constraint
Status: accepted (2026-11-28)
Context: The owner wants a fully free live deployment.
Decision: Target a single free ARM64 VM running k3s. All images build for arm64
and the whole stack has a memory budget, set from the measured baselines.
Consequences: The owner creates and verifies the cloud account; everything up to
that step is automated. Postgres, Redis and Prometheus are never public.

## ADR-007: Public visitors get a guided sandbox demo
Status: accepted (2026-11-28)
Decision: Visitors may break something and approve its fix only inside a
throwaway demo namespace, with rate limits and automatic reset. Everything else
needs the owner's login. LLM calls have global and per-visitor limits and fall
back to the template explainer.
Consequences: The caller-supplied identity gap (issues #59, #60) must be closed
before the stack is public.

## ADR-008: Measure before and after
Status: accepted (2026-11-28)
Decision: `harness/measure.py` records image size, idle and loaded memory,
ingest and correlation throughput, and cold start. Results live in
`baselines/`. Public claims quote only these numbers. End-to-end detection time
is set by scrape and window intervals, so it is not a headline.

## ADR-009: Built on PRs #78 and #79
Status: accepted (2026-11-28)
Context: Both pass CI. Merging them was left to the owner.
Decision: Branch `feat/rust-rewrite` from the head of #79, which contains #78.
Consequences: A squash merge of those PRs would require rebasing this branch.
The Docs page from #79 advertises an install script, CLI and hosted service
that do not exist; it must be rewritten before anything is public.

## ADR-010: Improvements are allowed during the port
Status: accepted (2026-11-28), narrows the faithful-port rule in ADR-003
Context: The owner: "if you think you can implement something in a better way,
you are free to do so."
Decision: The port may fix defects and improve design. Database keys, the schema
and bus message compatibility (ADR-003, ADR-005) stay, so mixed stacks keep
working. Every intentional behaviour difference is listed below with its reason,
and where a recorded case changes, in that unit's `known-diffs.jsonl`.
Consequences: "Matches Python" is a claim only for what the cases cover.

## Intentional differences so far (ADR-010)
- A timestamp without an offset is read as UTC. Python kept it naive, which
  could not be compared with offset-aware values.
- `/ingest` validates the whole batch before publishing. Python published the
  events ahead of a bad one and then failed.
- `/ingest` answers 422 for a bad event (Python: 500) and 503 when Redis is
  unavailable.
- Ingestion publishes a batch in one pipelined round trip.
- Consumers read in batches of 64. At-most-once acknowledges a batch before
  handling it, so a crash can lose that batch, not just one event.
- A consumer retries a failed publish instead of crashing.
- Deciding an approval or proposal is a single conditional update, so two
  deciders cannot both win.
- `memory` idempotency mode uses Redis, like `redis` mode.
- A runbook draft is validated without Pydantic's type coercion: `"2"` is not
  accepted where an integer is required; the model is asked to resubmit.
- Storage failures answer 503 instead of 500.
- RCA's embedding selector now runs. In the Python compose stack it was enabled
  but the image lacked the library, so ranking silently used rules only.
- With embedding selection configured and no model present, RCA refuses to start.
- `handling` is a closed type ("normal" | "quiet"); Python accepted any string.

## ADR-011: The per-service switch is a compose override
Status: accepted (2026-11-28), refines ADR-002
Decision: `deploy/docker-compose.rust.yml` swaps ported services to Rust images
built by `deploy/Dockerfile.rust` (distroless; the binary is its own
healthcheck). Removing a block returns that service to Python.
Consequences: Helm has no equivalent switch yet.

## ADR-012: Storage is Postgres or memory
Status: accepted (2026-11-28)
Context: Every deployment uses Postgres; the Python file stores served local
development only.
Decision: Do not port the file backends. The Rust schema file is generated from
Alembic and applies with `IF NOT EXISTS`, so it is a no-op on an existing
database.
Consequences: `STORE_BACKEND=file` now means memory.

## ADR-013: Python is removed from master once the port is complete
Status: accepted (2026-11-28)
Context: The owner: back up the current main to another branch, then remove the
Python code from main after porting.
Decision: `python-legacy` holds master as it was (commit 17f4107). When every
service runs on Rust, the Python code and its tooling are deleted from master.
Consequences: Recorded cases stay; the scripts that produced them need
`python-legacy` to run.
