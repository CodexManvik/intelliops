# Where the ML training data lives (Stream B)

Short version: your training data is in Postgres, in the `training_records` table. You don't need Kafka for any of this. Read straight from the table (or from the training store's `read_all()`), train your model, and point the result at the existing `CORRELATOR_KIND` seam. Kafka is a separate, still-unbuilt swap for the live message bus and it doesn't touch where anything is stored.

Here's the whole picture so nothing is ambiguous.

## Two different kinds of data, don't mix them up

**The bus (Redis Streams today) carries in-flight messages between services.** Topics: `telemetry.raw`, `situations.detected`, `situations.diagnosed`, `remediation.outcomes`, `situations.suppressed`. This is a pipe, not storage. A message is consumed and it's gone. Redis is just the transport; whether it's Redis or Kafka underneath changes nothing about what gets stored.

**Postgres holds the durable data** (behind `STORE_BACKEND=postgres`): `audit_records`, `training_records`, `playbooks`, `approvals`, `correlation_baseline`. This is what survives a restart, and it's what you query.

Your training data is in the second bucket. A message queue is the wrong tool for a training set anyway, because you want repeatable queries over the full labeled history, which is what a database gives you and a stream does not.

## How a training record gets created

1. The action service remediates an incident and publishes a `RemediationOutcome` to the `remediation.outcomes` bus topic.
2. The feedback service consumes that outcome and labels it. See `services/feedback/label.py`: `label_outcome()` derives a `signature` from the situation id and sets `worked = True` only on a clean success (a rollback or a failure counts as `worked = False`).
3. Feedback appends the resulting `TrainingRecord` to the training store (`services/feedback/consumer.py`, `store.append(...)`).
4. With `STORE_BACKEND=postgres`, that write lands in the `training_records` table. Durable, queryable, survives restarts.

Each row is one labeled example. The columns you'll care about: `situation_id`, `signature`, `playbook_id`, `result`, `worked` (bool), `ts`, plus the full record as JSONB in `payload`.

## How you read it for training

Two ways, both fine:

- Through the store: `stores.training_store.read_all()` returns the list of `TrainingRecord` objects. Correlation already does exactly this at boot to rebuild its reliability map, so there's a working example in `services/correlation/app.py` (the `retrain(...)` call).
- Straight SQL against `training_records` if you want to pull a big set or filter server-side. The `payload` column has the complete record if you need a field that isn't promoted to its own column.

That's your dataset. No queue, no consumer to write, no Kafka.

## What about Kafka, then

Kafka is Stream D's job and it isn't built yet. Only `RedisBus` exists in `common/bus.py`; there's no `KafkaBus`. When someone builds it, it's a drop-in replacement for the live event bus, selected by config, with Redis staying the default. It changes how in-flight events travel between services. It does not change where anything is stored, and it does not touch the training path. So for your work, ignore it. Redis or Kafka, the feedback service still labels outcomes and writes `TrainingRecord`s to Postgres the same way.

## One thing to decide before you start

The `training_records` rows are labeled remediation outcomes: "we ran playbook X against signature Y, and it worked or it didn't." That's the closed-loop learning signal, and it's already durable.

If your model instead needs the raw telemetry and log values (the actual metric readings and log lines flowing on `telemetry.raw`) as input features, be aware those are not persisted anywhere right now. They pass through the bus, correlation consumes them, and nothing writes them to a table. Two options if you need them:

- Point your training/feature pipeline at Prometheus directly. It already retains the metric history.
- Add a small consumer that tees `telemetry.raw` into a table or object store for offline training. That's new work, so flag it early if this is the route.

So the question back to you: do you need the labeled outcomes (already in Postgres, ready to go), the raw signal history (not captured yet), or both? That decides whether you can start today or whether we need to stand up the raw-telemetry capture first.

## Quick checklist for you

- Read training data from `training_records` in Postgres (or `training_store.read_all()`). Not from the bus, not from Kafka.
- Build your detector behind the `CORRELATOR_KIND` switch, default off, so the suite stays green (see the Stream B section in WORKPLAN.md).
- If you need raw telemetry as features, tell us, because that path isn't built.
- Run the stack with `STORE_BACKEND=postgres` locally to see real rows accumulate: drive an incident through the demo and watch the feedback service write records after each remediation.
