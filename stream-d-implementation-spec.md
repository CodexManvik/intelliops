# Stream D — Remaining Work Implementation Spec

Owner: Member C. Scope: Kafka binding, whole-stack K8s deploy, load/chaos testing.
Auth-at-the-edge and CI are already shipped — not covered here.

Verified against the actual repo (`CodexManvik/intelliops`, `master`) before writing
this, not against the workplan doc. Two premises in the original acceptance criteria
turned out to be wrong and are corrected below — read the "Findings" box in each
section before implementing, they change what "done" means.

---

## 0. Order of implementation

1. Bus contract test refactor (prerequisite for #2 — currently doesn't exist)
2. Kafka binding
3. K8s Helm chart / manifests
4. Load testing

Note: the original acceptance criteria list a chaos test as part of this
stream ("a documented chaos scenario — kill a service, show the bus/consumer-
group recovery," with numbers in OPERATIONS.md). It has been deliberately cut
from this spec at the owner's request and is not implemented below. If
acceptance is graded against the original criteria, that bullet will read as
unmet — this is a known, intentional gap, not an oversight.

---

## 1. Bus contract test refactor

### Finding
`tests/test_bus.py` and `tests/test_bus_consumer_name.py` test `RedisBus`
directly (`fakeredis`, Redis-specific assertions like URL prefixes and private
`._consumer` attribute reads). There is no shared "bus contract" a second
implementation can be dropped into. The acceptance criterion "Kafka binding
passes the same bus contract tests the Redis binding does" is not satisfiable
until this exists. Build this first.

### New file: `tests/test_bus_contract.py`

```python
"""Shared contract both bus bindings must satisfy. Parametrized so Redis and
Kafka run through the exact same assertions — this is the file the acceptance
criterion "same bus contract tests, config-swapped" refers to.

Kafka cases require a real broker (testcontainers) and are marked `kafka`,
excluded from the default fast test run the same way `postgres` is (see
pyproject.toml). Run with `pytest -m kafka` when Docker is available.
"""
from __future__ import annotations

import pytest
import fakeredis

from common.bus import RedisBus, KafkaBus


def _redis_bus(consumer_name="c1"):
    return RedisBus(client=fakeredis.FakeRedis(decode_responses=True), consumer_name=consumer_name)


@pytest.fixture(params=["redis", pytest.param("kafka", marks=pytest.mark.kafka)])
def bus_pair(request):
    """Yields (producer_bus, consumer_bus_factory) for one backend.

    consumer_bus_factory(name) lets a test spin up a second consumer in the
    same group, needed for the load-balancing / redelivery assertions.
    """
    if request.param == "redis":
        client = fakeredis.FakeRedis(decode_responses=True)
        yield (
            RedisBus(client=client, consumer_name="producer"),
            lambda name: RedisBus(client=client, consumer_name=name),
        )
    else:
        from testcontainers.kafka import KafkaContainer

        with KafkaContainer() as kafka:
            bootstrap = kafka.get_bootstrap_server()
            yield (
                KafkaBus(bootstrap_servers=bootstrap, consumer_name="producer"),
                lambda name: KafkaBus(bootstrap_servers=bootstrap, consumer_name=name),
            )


def test_publish_then_consume_round_trip(bus_pair):
    producer, consumer_factory = bus_pair
    consumer = consumer_factory("c1")
    producer.publish("topic.a", {"k": "v", "n": "1"})
    msg = next(consumer.consume("topic.a", group="g1"))
    assert msg["k"] == "v"
    assert msg["n"] == "1"


def test_consumer_group_load_balances_across_members(bus_pair):
    """Two consumers in the same group must not both receive the same message."""
    producer, consumer_factory = bus_pair
    c1 = consumer_factory("c1")
    c2 = consumer_factory("c2")
    for i in range(10):
        producer.publish("topic.b", {"i": str(i)})
    it1, it2 = c1.consume("topic.b", group="g2"), c2.consume("topic.b", group="g2")
    seen = {next(it1)["i"], next(it2)["i"]}
    assert len(seen) == 2  # different messages, not both got the same one


def test_two_groups_each_get_all_messages(bus_pair):
    """Independent consumer groups are independent — both see every message."""
    producer, consumer_factory = bus_pair
    producer.publish("topic.c", {"x": "1"})
    ga = consumer_factory("ga-c1").consume("topic.c", group="ga")
    gb = consumer_factory("gb-c1").consume("topic.c", group="gb")
    assert next(ga)["x"] == "1"
    assert next(gb)["x"] == "1"


def test_group_recreated_is_idempotent(bus_pair):
    """Creating the same group twice must not raise (BUSYGROUP-equivalent)."""
    producer, consumer_factory = bus_pair
    producer.publish("topic.d", {"a": "1"})
    consumer_factory("c1").consume("topic.d", group="dup").__next__()
    # second call to consume() on the same group must not raise on group-create
    next(consumer_factory("c2").consume("topic.d", group="dup"), None)
```

Add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "postgres: tests that require a real Postgres (testcontainers + Docker)",
    "kafka: tests that require a real Kafka broker (testcontainers + Docker)",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "ruff>=0.7",
    "fakeredis>=2.37.0",
    "testcontainers[postgres]>=4.0",
    "testcontainers[kafka]>=4.0",
]
```

CI note: `.github/workflows/ci.yml` currently runs `pytest -q -m "not postgres"`.
Change to `pytest -q -m "not postgres and not kafka"` so the new marker doesn't
break the existing job — do **not** silently let Kafka tests run in the fast
job, they need a broker and will hang/fail without Docker context there.

---

## 2. Kafka binding

### Findings that shaped this design
- Dockerfile is `python:3.11-slim`, no apt/native-toolchain step →
  **`kafka-python` (pure Python)**, not `confluent-kafka` (needs `librdkafka`).
  Lower throughput than confluent's client; irrelevant at this project's scale.
- `RedisBus.consume` acks (`xack`) *before* yielding to the caller — i.e. it is
  at-most-once in practice, not at-least-once, despite using consumer groups.
  `KafkaBus` replicates this exactly (`enable_auto_commit=True`) so the two
  backends are actually equivalent, not just superficially similar. Document
  this gap in OPERATIONS.md rather than let either binding imply a durability
  guarantee it doesn't have.

### Edit: `common/config.py`
Add next to the existing `auth_mode` switch:
```python
    # --- Bus binding (ADR-001: Redis for dev, Kafka for prod) ---
    bus_backend: str = "redis"  # "redis" | "kafka"
    kafka_bootstrap_servers: str = "localhost:9092"
```

### Edit: `common/bus.py`
Full file (Redis class unchanged, Kafka class + updated factory added):

```python
"""Event-bus client. Redis Streams is the dev binding of the BusClient protocol,
Kafka is the prod binding (see ADR-001). Both bindings share the same
at-most-once delivery semantics: the offset/entry is acknowledged as soon as
it's read, not after the caller finishes processing it. A crash mid-processing
loses the in-flight message on either backend. If you need true at-least-once,
that requires changing the ack/commit point on *both* bindings together, not
just one — don't fix it in only one place and call it durable.

Consumer groups make delivery durable and load-balanced. `consume` blocks for
new entries and yields decoded field dicts. A `make_bus` factory lets services
stay unaware of the concrete implementation (see ADR-001, ADR-005).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import redis

from common.config import Settings


class RedisBus:
    def __init__(self, client: redis.Redis, consumer_name: str = "c1") -> None:
        self._r = client
        self._consumer = consumer_name

    def publish(self, topic: str, message: dict) -> None:
        self._r.xadd(topic, message)

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        try:
            self._r.xgroup_create(topic, group, id="0", mkstream=True)
        except redis.ResponseError as exc:  # group already exists
            if "BUSYGROUP" not in str(exc):
                raise
        while True:
            resp = self._r.xreadgroup(group, self._consumer, {topic: ">"}, count=1, block=1000)
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    self._r.xack(topic, group, entry_id)
                    yield fields


class KafkaBus:
    """Kafka binding of BusClient. Topics are plain Kafka topics; Redis
    "groups" map directly onto Kafka consumer groups, which give the same
    load-balancing behaviour (one message to one member per group) and the
    same independent-groups-see-everything behaviour Redis Streams gives us.
    """

    def __init__(self, bootstrap_servers: str, consumer_name: str = "c1") -> None:
        self._bootstrap = bootstrap_servers
        self._consumer_name = consumer_name
        self._producer = None  # lazy: not every service publishes
        self._consumers: dict[tuple[str, str], object] = {}

    def _get_producer(self):
        if self._producer is None:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=self._bootstrap,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
        return self._producer

    def publish(self, topic: str, message: dict) -> None:
        self._get_producer().send(topic, value=message)
        self._get_producer().flush()

    def consume(self, topic: str, group: str) -> Iterator[dict]:
        from kafka import KafkaConsumer

        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=group,
            client_id=self._consumer_name,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=True,  # matches RedisBus's ack-before-processing semantics
        )
        for record in consumer:
            yield record.value


def make_bus(settings: Settings, consumer_name: str = "c1"):
    if settings.bus_backend == "kafka":
        return KafkaBus(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            consumer_name=consumer_name,
        )
    return RedisBus(
        client=redis.from_url(settings.redis_url, decode_responses=True),
        consumer_name=consumer_name,
    )
```

Add to `pyproject.toml` main `dependencies`:
```toml
    "kafka-python>=2.0.2",
```

### Edit: `deploy/docker-compose.yml`
Add a Kafka service (KRaft mode, no Zookeeper needed on modern images) and wire
`BUS_BACKEND=kafka` / `KAFKA_BOOTSTRAP_SERVERS` as an opt-in profile or a second
compose file (`docker-compose.kafka.yml`) — don't make Kafka the compose
default, Redis stays default per the acceptance criteria. Recommend:

```yaml
  kafka:
    image: bitnami/kafka:3.7
    environment:
      KAFKA_CFG_NODE_ID: "0"
      KAFKA_CFG_PROCESS_ROLES: controller,broker
      KAFKA_CFG_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
      KAFKA_CFG_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: 0@kafka:9093
      KAFKA_CFG_CONTROLLER_LISTENER_NAMES: CONTROLLER
    ports: ["9092:9092"]
```
Gate it behind a compose profile (`profiles: ["kafka"]`) so `docker compose up`
without `--profile kafka` behaves exactly as it does today.

---

## 3. K8s whole-stack deploy (`deploy/k8s/platform/`)

### Finding
`deploy/Dockerfile` is a single generic image parameterized by `SERVICE_MODULE`
and `PORT` env vars (`CMD uvicorn $SERVICE_MODULE ...`). This means the Helm
chart is **one Deployment template + a values list**, not nine bespoke
manifests. Existing `deploy/k8s/` only has `demo-app/` and `prometheus/` — this
deploys the *target being remediated*, not IntelliOps itself. Don't conflict
with that directory; put everything under `deploy/k8s/platform/`.

### Structure
```
deploy/k8s/platform/
  Chart.yaml
  values.yaml
  templates/
    _helpers.tpl
    service-deployment.yaml   # one template, ranges over values.services
    redis.yaml
    postgres.yaml
    migrate-job.yaml          # pre-install hook, mirrors compose's `migrate` one-shot
    configmap.yaml            # all env switches in one place
```

### `values.yaml`
```yaml
image:
  repository: intelliops
  tag: latest

env:
  AUTH_MODE: "off"
  STORE_BACKEND: postgres
  BUS_BACKEND: redis

services:
  - name: ingestion
    module: services.ingestion.app:app
    port: 8001
  - name: correlation
    module: services.correlation.app:app
    port: 8002
  - name: rca
    module: services.rca.app:app
    port: 8003
  - name: governance
    module: services.governance.app:app
    port: 8004
  - name: action
    module: services.action.app:app
    port: 8005
  - name: read
    module: services.read.app:app
    port: 8006
# confirm exact module paths/ports against deploy/docker-compose.yml before applying —
# copy them directly from there rather than retyping.

postgres:
  image: postgres:16
redis:
  image: redis:7
```

### `templates/service-deployment.yaml` (the key template)
```yaml
{{- range .Values.services }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}
spec:
  replicas: 1
  selector:
    matchLabels: {app: {{ .name }}}
  template:
    metadata:
      labels: {app: {{ .name }}}
    spec:
      containers:
        - name: {{ .name }}
          image: "{{ $.Values.image.repository }}:{{ $.Values.image.tag }}"
          env:
            - {name: SERVICE_MODULE, value: {{ .module }}}
            - {name: PORT, value: "{{ .port }}"}
          envFrom:
            - configMapRef: {name: intelliops-env}
          ports:
            - containerPort: {{ .port }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ .name }}
spec:
  selector: {app: {{ .name }}}
  ports:
    - port: {{ .port }}
{{- end }}
```
`migrate-job.yaml` should be a Helm pre-install/pre-upgrade hook Job running
the same migrate command the compose `migrate` one-shot service runs — check
`deploy/docker-compose.yml`'s `migrate` service definition for the exact
command before writing this, copy it rather than re-deriving it.

### Acceptance target
"Documented one-command deploy" = `helm install intelliops deploy/k8s/platform/`
against a kind cluster, documented in `docs/OPERATIONS.md`, with the migrate
Job completing before app pods report ready.

---

## 4. Load testing

Scope cut: chaos testing removed from this spec at owner's request (see note
in Section 0). `scripts/chaos.sh` already in the repo is unrelated — it's a
demo-driver script (breaks the demo-app, walks a human through the Approve
button), not a resilience test, and isn't touched by this section.

### New file: `scripts/load-test.sh`
Drive N synthetic incidents/minute through the ingestion endpoint and measure
end-to-end throughput. Pure throughput only — no kill/recovery logic.
```bash
#!/usr/bin/env bash
# Usage: ./scripts/load-test.sh [incidents_per_minute] [duration_seconds]
# Posts synthetic telemetry to the ingestion service at the given rate and
# reports: events sent, events observed at the read API, end-to-end latency
# (p50/p95). Writes results to stdout AND appends a timestamped block to
# docs/OPERATIONS.md.
```
Confirm the exact ingestion POST endpoint/payload shape against
`services/ingestion/app.py` and `common/contracts.py` before writing the
payload generator — don't guess the schema.

### `docs/OPERATIONS.md` additions
- One table with every env switch (`AUTH_MODE`, `STORE_BACKEND`, `BUS_BACKEND`,
  plus existing `REMEDIATOR_MODE`) — confirm the full existing switch list in
  the current `docs/OPERATIONS.md` before writing the table, don't recreate
  switches that already have rows.
- A "Delivery guarantees" subsection stating the at-most-once finding from
  Section 2 plainly, for both bindings.
- Load test results block (numbers, not claims).
- `helm install` one-command deploy instructions from Section 3.

---

## Open questions Kiro should confirm against the live repo before implementing
(these are places I made a reasonable call but didn't have a live cluster/broker to verify against)

1. Exact `migrate` service command in `deploy/docker-compose.yml` — copy verbatim into the Helm pre-install Job.
2. Exact module/port list for all six services — copy verbatim from `deploy/docker-compose.yml`, the list above is illustrative.
3. Ingestion POST endpoint schema for the load-test payload generator — read `services/ingestion/app.py` + `common/contracts.py` directly.
4. Whether `kafka-python` (unmaintained upstream for ~2 years as of last check) is acceptable for a graded/pitched project vs. switching to `aiokafka` (async, would require wrapping in a sync generator) — flagged, not decided, here.
