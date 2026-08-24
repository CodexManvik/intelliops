# Implementation Plan: Stream D Implementation

## Overview

Implement the four Stream D deliverables in dependency order: (1) bus contract test suite, (2) Kafka binding with config and factory updates, (3) Kubernetes Helm chart, and (4) load-testing script with OPERATIONS.md additions. All Python code targets `python:3.11-slim` and uses `kafka-python>=2.0.2` for the Kafka client.

## Tasks

- [x] 1. Add Kafka dependencies and marker configuration
  - Add `"kafka-python>=2.0.2"` to the `[project] dependencies` array in `pyproject.toml`
  - Add `"testcontainers[kafka]>=4.0"` to the `[dependency-groups] dev` array alongside the existing `testcontainers[postgres]>=4.0` entry
  - Add `"kafka: tests that require a real Kafka broker (testcontainers + Docker)"` to `[tool.pytest.ini_options] markers` in `pyproject.toml`
  - _Requirements: 2.2, 2.3, 4.6_

- [x] 2. Extend Settings with Kafka configuration fields
  - [x] 2.1 Add `bus_backend` and `kafka_bootstrap_servers` fields to `Settings` in `common/config.py`
    - Append `bus_backend: str = "redis"` (env var `INTELLIOPS_BUS_BACKEND`) to the `Settings` class
    - Append `kafka_bootstrap_servers: str = "localhost:9092"` (env var `INTELLIOPS_KAFKA_BOOTSTRAP_SERVERS`) to the `Settings` class
    - No Pydantic validator — the `make_bus` factory is the enforcement point
    - _Requirements: 3.1, 3.2_

  - [x] 2.2 Write unit tests for Settings additions
    - Test that `bus_backend` defaults to `"redis"` and reads from `INTELLIOPS_BUS_BACKEND`
    - Test that `kafka_bootstrap_servers` defaults to `"localhost:9092"` and reads from `INTELLIOPS_KAFKA_BOOTSTRAP_SERVERS`
    - _Requirements: 3.1, 3.2_

- [x] 3. Implement KafkaBus class and update make_bus factory
  - [x] 3.1 Implement `KafkaBus` class in `common/bus.py`
    - Add `KafkaBus.__init__(self, bootstrap_servers: str, consumer_name: str = "c1") -> None`
    - Add `KafkaBus.publish(self, topic: str, message: dict) -> None` with lazy `KafkaProducer` import, JSON serialization, `send` + `flush`
    - Add `KafkaBus.consume(self, topic: str, group: str) -> Iterator[dict]` with lazy `KafkaConsumer` import, `group_id=group`, `client_id=consumer_name`, `auto_offset_reset="earliest"`, `enable_auto_commit=True`, yielding deserialized dicts
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7_

  - [x] 3.2 Update `make_bus` factory in `common/bus.py`
    - Change return type annotation to `RedisBus | KafkaBus`
    - Add `elif settings.bus_backend == "kafka": return KafkaBus(bootstrap_servers=settings.kafka_bootstrap_servers, consumer_name=consumer_name)`
    - Add `else: raise ValueError(f"Unknown bus backend: {settings.bus_backend!r}. Expected 'redis' or 'kafka'.")`
    - _Requirements: 3.3, 3.4, 3.5_

  - [x] 3.3 Write unit tests for KafkaBus and make_bus in `tests/test_bus.py` (or equivalent unit test file)
    - `test_kafkabus_satisfies_protocol` — `isinstance(KafkaBus(...), BusClient)`
    - `test_make_bus_returns_redis_bus` — `make_bus(settings_with_backend("redis"))` returns `RedisBus`
    - `test_make_bus_returns_kafka_bus` — `make_bus(settings_with_backend("kafka"))` returns `KafkaBus`
    - `test_make_bus_raises_on_unknown_backend` — `make_bus(settings_with_backend("nats"))` raises `ValueError`
    - `test_lazy_import` — import `KafkaBus`; assert `"kafka"` not in `sys.modules`
    - `test_enable_auto_commit` — mock `KafkaConsumer`; assert called with `enable_auto_commit=True`
    - _Requirements: 4.1, 4.7, 3.3, 3.4, 3.5_

  - [x] 3.4 Write property test for make_bus dispatch correctness
    - **Property 5: make_bus dispatch correctness**
    - **Validates: Requirements 3.1, 3.3, 3.4**
    - Use `@given(st.sampled_from(["redis", "kafka"]))` to verify `make_bus` returns the correct concrete type and satisfies `BusClient`

  - [x] 3.5 Write property test for invalid backend rejection
    - **Property 6: Invalid backend rejection**
    - **Validates: Requirements 3.5**
    - Use `@given(st.text().filter(lambda s: s not in ("redis", "kafka")))` to verify `make_bus` raises `ValueError`

- [x] 4. Checkpoint — Ensure all unit tests pass
  - Ensure all tests pass (`uv run pytest -q -m "not postgres and not kafka"`), ask the user if questions arise.

- [x] 5. Create bus contract test suite
  - [x] 5.1 Create `tests/test_bus_contract.py` with fixtures and `pytest_configure` marker registration
    - Register `kafka` marker via `pytest_configure(config)` with `config.addinivalue_line`
    - Implement session-scoped `kafka_bootstrap` fixture using `testcontainers.kafka.KafkaContainer`; skip gracefully if Docker unavailable
    - Implement parametrized `bus` fixture with `params=["redis", pytest.param("kafka", marks=pytest.mark.kafka)]`
      - `redis`: use `fakeredis.FakeRedis(decode_responses=True)` with `RedisBus`
      - `kafka`: use `KafkaBus(bootstrap_servers=kafka_bootstrap)`
    - Define Hypothesis strategies: `message_strategy` (dict of alphanumeric str keys/values, 1–8 pairs), `topic_strategy` (alphanumeric, 3–30 chars), `group_strategy`
    - _Requirements: 1.1, 1.2, 1.3, 1.8_

  - [x] 5.2 Implement `test_satisfies_protocol` and `test_publish_consume_roundtrip`
    - `test_satisfies_protocol(bus)` — assert `isinstance(bus, BusClient)`
    - `test_publish_consume_roundtrip(bus, message, topic)` — publish then consume; assert received dict equals published dict
    - Annotate: `# Feature: stream-d-implementation, Property 1: bus publish/consume round-trip`
    - _Requirements: 1.1, 1.4_

  - [x] 5.3 Write property test for bus publish/consume round-trip
    - **Property 1: Bus publish/consume round-trip**
    - **Validates: Requirements 1.4, 4.3, 4.4**
    - `@given(message=message_strategy, topic=topic_strategy)` with `@settings(max_examples=100)`

  - [x] 5.4 Implement `test_consumer_group_load_balancing`
    - Publish N ≥ 2 distinct messages; create two consumers in same group; assert received sets are disjoint and their union equals all published messages
    - Annotate: `# Feature: stream-d-implementation, Property 2: consumer group load-balancing`
    - _Requirements: 1.5_

  - [x] 5.5 Write property test for consumer group load-balancing
    - **Property 2: Consumer group load-balancing**
    - **Validates: Requirements 1.5**
    - `@given(messages=st.lists(message_strategy, min_size=2, max_size=8, unique=True), topic=topic_strategy)`

  - [x] 5.6 Implement `test_independent_groups_fanout`
    - Publish one message; create two consumers under different group names; assert both receive a dict with identical field values
    - Annotate: `# Feature: stream-d-implementation, Property 3: independent groups fan-out`
    - _Requirements: 1.6_

  - [x] 5.7 Write property test for independent groups fan-out
    - **Property 3: Independent groups fan-out**
    - **Validates: Requirements 1.6**
    - `@given(message=message_strategy, topic=topic_strategy, g1=group_strategy, g2=group_strategy.filter(lambda g: g != g1))`

  - [x] 5.8 Implement `test_idempotent_group_creation`
    - Call `consume` on same topic/group twice; assert no exception raised on second call
    - Annotate: `# Feature: stream-d-implementation, Property 4: idempotent consumer group creation`
    - _Requirements: 1.7_

  - [x] 5.9 Write property test for idempotent consumer group creation
    - **Property 4: Idempotent consumer group creation**
    - **Validates: Requirements 1.7**
    - `@given(topic=topic_strategy, group=group_strategy)`

- [x] 6. Update CI pipeline marker filter
  - Edit `.github/workflows/ci.yml` to change the fast test job's pytest invocation from `pytest -q -m "not postgres"` to `pytest -q -m "not postgres and not kafka"`
  - _Requirements: 2.1_

- [x] 7. Checkpoint — Ensure all non-kafka tests pass
  - Ensure `uv run pytest -q -m "not postgres and not kafka"` passes cleanly, ask the user if questions arise.

- [x] 8. Add Kafka service to docker-compose.yml
  - Append the `kafka` service block to `deploy/docker-compose.yml` using image `bitnami/kafka:3.7` with KRaft mode environment variables: `KAFKA_CFG_PROCESS_ROLES: controller,broker`, `KAFKA_CFG_NODE_ID: "0"`, `KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: 0@kafka:9093`, and listener configuration for port 9092
  - Gate the service behind the `kafka` compose profile so default `docker compose up` is unaffected
  - Add healthcheck: `kafka-topics.sh --bootstrap-server localhost:9092 --list` (interval 10s, timeout 5s, retries 10, start_period 30s)
  - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [x] 9. Create Kubernetes Helm chart
  - [x] 9.1 Create `deploy/k8s/platform/Chart.yaml` and `deploy/k8s/platform/values.yaml`
    - `Chart.yaml`: `apiVersion: v2`, `name: intelliops`, description, `type: application`, `version: 0.1.0`, `appVersion: "latest"`
    - `values.yaml`: `image` block (`repository: intelliops`, `tag: latest`, `pullPolicy: IfNotPresent`); `services` list with all 7 services (`ingestion`, `correlation`, `rca`, `action`, `governance`, `feedback`, `read`) each with `name`, `module`, and both `port`/`externalPort`; `env` defaults (`AUTH_MODE: "off"`, `STORE_BACKEND: postgres`, `BUS_BACKEND: redis`); `redis.image: redis:7`; `postgres` block (`image: postgres:16`, `user`, `password`, `db`)
    - _Requirements: 8.1, 8.5, 8.6_

  - [x] 9.2 Create `deploy/k8s/platform/templates/_helpers.tpl`
    - Define `intelliops.fullname`, `intelliops.labels`, and `intelliops.selectorLabels` named templates
    - _Requirements: 8.1, 8.2_

  - [x] 9.3 Create `deploy/k8s/platform/templates/configmap.yaml`
    - Render ConfigMap named `intelliops-env` with keys: `INTELLIOPS_AUTH_MODE`, `INTELLIOPS_STORE_BACKEND`, `INTELLIOPS_BUS_BACKEND`, `INTELLIOPS_REDIS_URL`, `INTELLIOPS_DATABASE_URL` populated from `values.env` and `values.postgres`
    - _Requirements: 8.7_

  - [x] 9.4 Create `deploy/k8s/platform/templates/service-deployment.yaml`
    - Range over `$.Values.services`; emit one `Deployment` + one `Service` per entry
    - Each Deployment: sets `SERVICE_MODULE` from `service.module` and `PORT` from `service.port` as env vars; mounts `intelliops-env` ConfigMap as `envFrom`; includes liveness probe at `/health` with `initialDelaySeconds: 10`, `periodSeconds: 10`
    - _Requirements: 8.3, 8.4, 8.5_

  - [x] 9.5 Create `deploy/k8s/platform/templates/redis.yaml` and `deploy/k8s/platform/templates/postgres.yaml`
    - `redis.yaml`: Deployment + Service for Redis using `values.redis.image`, port 6379
    - `postgres.yaml`: Deployment + Service for Postgres using `values.postgres.image`, env vars `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` from values, port 5432
    - _Requirements: 8.8, 8.9_

  - [x] 9.6 Create `deploy/k8s/platform/templates/migrate-job.yaml`
    - Kubernetes `Job` with annotations `helm.sh/hook: pre-install,pre-upgrade` and `helm.sh/hook-delete-policy: before-hook-creation`
    - Container runs `alembic upgrade head`, uses `intelliops` image, mounts `intelliops-env` ConfigMap as `envFrom`
    - `restartPolicy: OnFailure`
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 10. Create load-testing script and update OPERATIONS.md
  - [x] 10.1 Create `scripts/load-test.sh`
    - Add shebang (`#!/usr/bin/env bash`), usage comment, and `chmod +x` note
    - Accept two optional positional args: `incidents_per_minute` (default 60) and `duration_seconds` (default 60)
    - Implement rate-controlled loop using `curl` to POST `IngestBatch` payloads to `POST /ingest`; each payload: `{"events": [{"source": "load-test", "kind": "metric", "name": "cpu_usage", "value": 0.75, "labels": {}, "ts": "<ISO-8601 UTC now>"}]}`
    - Collect per-request latencies; after loop, compute p50 and p95 using `awk`
    - Print `Total events:`, `p50 latency:`, `p95 latency:` to stdout
    - Append a timestamped results block to `docs/OPERATIONS.md` (warn on stderr if not writable)
    - Handle `duration_seconds=0` by exiting immediately with `total_sent=0`
    - Use only POSIX tools: `curl`, `date`, `awk`, `sort`, `bc`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [x] 10.2 Update `docs/OPERATIONS.md` with required sections
    - Add "Delivery guarantees" subsection: state both `RedisBus` and `KafkaBus` use at-most-once delivery; state that a crash between read and processing loses the in-flight message; state that upgrading to at-least-once requires changing ack/commit point on both bindings
    - Add "Kubernetes deploy" section with command `helm install intelliops deploy/k8s/platform/`, prerequisites (kind cluster running, `intelliops` image built and loaded)
    - Add consolidated env-switch reference table covering `AUTH_MODE`, `STORE_BACKEND`, `BUS_BACKEND`, `REMEDIATOR_MODE` with accepted values, defaults, and descriptions; cross-reference existing auth section instead of duplicating it
    - _Requirements: 6.1, 6.2, 6.3, 10.1, 10.2, 12.1, 12.2_

- [x] 11. Final checkpoint — Ensure all tests pass
  - Ensure `uv run pytest -q -m "not postgres and not kafka"` passes; run `helm lint deploy/k8s/platform/` to validate the Helm chart; ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests (Properties 1–6) are defined in the design document's Correctness Properties section and use Hypothesis with `max_examples=100`
- Unit tests and property tests are complementary; both target `tests/test_bus_contract.py` for contract coverage
- Kafka-parametrized test cases must carry `pytest.mark.kafka` and are excluded from the fast CI job
- The Helm chart uses `helm lint` for static validation; full cluster testing requires a live kind cluster

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1", "9.1"] },
    { "id": 1, "tasks": ["2.2", "3.1", "9.2"] },
    { "id": 2, "tasks": ["3.2", "9.3"] },
    { "id": 3, "tasks": ["3.3", "3.4", "3.5", "5.1", "9.4"] },
    { "id": 4, "tasks": ["5.2", "5.4", "5.6", "5.8", "9.5"] },
    { "id": 5, "tasks": ["5.3", "5.5", "5.7", "5.9", "9.6"] },
    { "id": 6, "tasks": ["6", "8", "10.1"] },
    { "id": 7, "tasks": ["10.2"] }
  ]
}
```
