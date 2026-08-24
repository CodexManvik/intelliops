# Requirements Document

## Introduction

Stream D completes the remaining platform work for IntelliOps: a Kafka event-bus
binding as an alternative to the existing Redis Streams binding, a whole-stack
Kubernetes Helm chart for one-command cluster deployment, a load-testing script
that measures end-to-end throughput, and the bus contract test suite that makes
both bindings verifiably interchangeable. Auth-at-the-edge and CI are already
shipped and are not re-specified here.

The four deliverables must be built in dependency order:
1. Bus contract test suite (prerequisite for verifying the Kafka binding)
2. Kafka binding
3. Kubernetes Helm chart (`deploy/k8s/platform/`)
4. Load-testing script and OPERATIONS.md additions

Chaos testing was deliberately cut from this scope at the owner's request.

---

## Glossary

- **BusClient**: The structural protocol (defined in `common/interfaces.py`) that both bus bindings must satisfy: `publish(topic, message)` and `consume(topic, group)` methods.
- **RedisBus**: The existing Redis Streams implementation of BusClient in `common/bus.py`.
- **KafkaBus**: The new Kafka implementation of BusClient to be added to `common/bus.py`.
- **Bus_Contract_Suite**: The parametrized pytest file `tests/test_bus_contract.py` that exercises BusClient assertions against both bindings.
- **CI_Pipeline**: The GitHub Actions workflow defined in `.github/workflows/ci.yml`.
- **Settings**: The Pydantic settings class in `common/config.py` that reads configuration from environment variables with the `INTELLIOPS_` prefix.
- **make_bus**: The factory function in `common/bus.py` that constructs the appropriate bus binding based on `Settings.bus_backend`.
- **Helm_Chart**: The Helm chart at `deploy/k8s/platform/` that deploys all IntelliOps platform services to a Kubernetes cluster.
- **Migrate_Job**: A Kubernetes Job that runs `alembic upgrade head` as a Helm pre-install/pre-upgrade hook, mirroring the `migrate` one-shot service in `deploy/docker-compose.yml`.
- **Load_Test_Script**: The shell script at `scripts/load-test.sh` that drives synthetic incidents through the ingestion endpoint and reports throughput metrics.
- **Ingestion_Service**: The service at `services/ingestion/app.py` that receives telemetry via `POST /ingest` and publishes normalized events onto `telemetry.raw`.
- **IngestBatch**: The request body schema for `POST /ingest`: `{"events": [<raw telemetry dicts>]}`.
- **OPERATIONS.md**: The operations reference document at `docs/OPERATIONS.md` maintained by Stream D.
- **KRaft**: Kafka's built-in consensus mode (no Zookeeper dependency), used by the `bitnami/kafka:3.7` image.
- **Consumer_Group**: A named group of consumers that collectively receive each message exactly once (load-balancing); independent groups each receive all messages.
- **at-most-once**: The delivery guarantee where a message is acknowledged before the caller processes it; a crash mid-processing loses the in-flight message. Both bindings share this semantic.
- **testcontainers**: A Python library that spins up real Docker containers (Kafka, Postgres) inside pytest sessions for integration tests.

---

## Requirements

### Requirement 1: Bus Contract Test Suite

**User Story:** As a developer, I want a parametrized test suite that exercises
both bus bindings through the same assertions, so that I can verify KafkaBus
satisfies the same contract as RedisBus without maintaining two separate test files.

#### Acceptance Criteria

1. THE Bus_Contract_Suite SHALL be located at `tests/test_bus_contract.py` and parametrized over both `"redis"` and `"kafka"` backends.
2. WHEN the `redis` parameter is selected, THE Bus_Contract_Suite SHALL use `fakeredis.FakeRedis` as the Redis client, requiring no external services.
3. WHEN the `kafka` parameter is selected, THE Bus_Contract_Suite SHALL use a `testcontainers.kafka.KafkaContainer` to provision a real Kafka broker for the test session.
4. THE Bus_Contract_Suite SHALL assert that publishing a message and then consuming it from the same topic returns a dict with the same field values (round-trip property).
5. THE Bus_Contract_Suite SHALL assert that two consumers in the same Consumer_Group receive distinct messages when multiple messages are published to the same topic (load-balancing property).
6. THE Bus_Contract_Suite SHALL assert that two consumers each in a different Consumer_Group both receive the same message published to a topic (independent-groups property).
7. THE Bus_Contract_Suite SHALL assert that calling `consume` on a topic with a Consumer_Group that already exists does not raise an exception (idempotent group creation).
8. THE Bus_Contract_Suite SHALL mark all Kafka-parametrized test cases with the `kafka` pytest marker.
9. WHEN the `kafka` pytest marker is present on a test case, THE CI_Pipeline SHALL exclude that test case from the default fast test job.

### Requirement 2: CI Pipeline Marker Update

**User Story:** As a developer, I want the CI fast-test job to exclude Kafka tests
the same way it already excludes Postgres tests, so that the job does not hang or
fail when a Docker-hosted Kafka broker is unavailable.

#### Acceptance Criteria

1. THE CI_Pipeline's `test` job SHALL run `pytest -q -m "not postgres and not kafka"` instead of the current `pytest -q -m "not postgres"`.
2. THE `pyproject.toml` SHALL declare a `kafka` marker with a human-readable description under `[tool.pytest.ini_options]` markers, consistent with the existing `postgres` marker declaration.
3. WHEN `testcontainers[kafka]>=4.0` is added as a dev dependency, THE `pyproject.toml` SHALL list it in the `[dependency-groups] dev` array alongside the existing `testcontainers[postgres]>=4.0` entry.

### Requirement 3: Kafka Binding — Configuration

**User Story:** As an operator, I want to switch the event-bus backend from Redis
to Kafka by setting an environment variable, so that I can run the production stack
against Kafka without changing application code.

#### Acceptance Criteria

1. THE Settings class SHALL expose a `bus_backend` field with default value `"redis"` that accepts `"redis"` or `"kafka"`, readable from the `INTELLIOPS_BUS_BACKEND` environment variable.
2. THE Settings class SHALL expose a `kafka_bootstrap_servers` field with default value `"localhost:9092"`, readable from the `INTELLIOPS_KAFKA_BOOTSTRAP_SERVERS` environment variable.
3. WHEN `bus_backend` is `"redis"`, THE `make_bus` factory SHALL return a `RedisBus` instance constructed from `Settings.redis_url`.
4. WHEN `bus_backend` is `"kafka"`, THE `make_bus` factory SHALL return a `KafkaBus` instance constructed from `Settings.kafka_bootstrap_servers`.
5. WHEN `bus_backend` is neither `"redis"` nor `"kafka"`, THE `make_bus` factory SHALL raise a `ValueError` with a descriptive message identifying the invalid value.

### Requirement 4: Kafka Binding — KafkaBus Implementation

**User Story:** As a developer, I want a KafkaBus class that satisfies BusClient
using `kafka-python`, so that the platform can use Kafka as a drop-in bus backend
on the existing `python:3.11-slim` Docker image without native library dependencies.

#### Acceptance Criteria

1. THE `KafkaBus` class SHALL be defined in `common/bus.py` and SHALL satisfy the `BusClient` structural protocol.
2. THE `KafkaBus` class SHALL accept `bootstrap_servers: str` and `consumer_name: str` as constructor arguments.
3. WHEN `KafkaBus.publish(topic, message)` is called, THE `KafkaBus` SHALL serialize `message` as JSON and send it to the named Kafka topic, flushing the producer before returning.
4. WHEN `KafkaBus.consume(topic, group)` is called, THE `KafkaBus` SHALL create a `KafkaConsumer` with `group_id=group`, `client_id=consumer_name`, `auto_offset_reset="earliest"`, and `enable_auto_commit=True`, and SHALL yield each record's deserialized JSON value as a dict.
5. THE `KafkaBus` implementation SHALL use `enable_auto_commit=True` to replicate the at-most-once delivery semantics of `RedisBus`, where the offset is committed as soon as the record is read rather than after the caller finishes processing.
6. THE `pyproject.toml` main `dependencies` array SHALL include `"kafka-python>=2.0.2"`.
7. THE `KafkaBus` SHALL import `kafka.KafkaProducer` and `kafka.KafkaConsumer` lazily (inside methods) so that the `kafka-python` package is only imported when a Kafka bus is actually instantiated.

### Requirement 5: Kafka Client Library Selection

**User Story:** As a developer, I want the choice of Kafka client library
explicitly documented, so that implementors do not pick one arbitrarily and
the tradeoff is a recorded decision rather than a silent assumption.

#### Acceptance Criteria

1. THE `KafkaBus` implementation SHALL use `kafka-python>=2.0.2` as the Kafka client library.
2. THE decision to use `kafka-python` over `aiokafka` SHALL be accepted on the following grounds: the `deploy/Dockerfile` base image (`python:3.11-slim`) carries no native toolchain, ruling out `confluent-kafka` (requires `librdkafka`); `aiokafka` would require wrapping an async iterator in a synchronous generator to satisfy the `BusClient` protocol, adding complexity for no functional gain at this project's scale; throughput difference is irrelevant at this project's traffic level.
3. THE acknowledged limitation SHALL be recorded: `kafka-python` has had limited upstream maintenance activity as of mid-2026. IF the project is pitched or graded in a context where library maintenance posture is evaluated, THE team SHALL be prepared to justify this choice or migrate to `aiokafka` with the sync-wrapper pattern.

### Requirement 6: Kafka Delivery Guarantee Documentation

**User Story:** As an operator, I want the delivery guarantee of both bus bindings
documented plainly in OPERATIONS.md, so that I do not incorrectly assume either
binding provides at-least-once delivery.

#### Acceptance Criteria

1. THE OPERATIONS.md SHALL contain a "Delivery guarantees" subsection that states both `RedisBus` and `KafkaBus` use at-most-once delivery semantics.
2. THE OPERATIONS.md delivery-guarantees subsection SHALL state that a crash between reading a message and finishing processing loses the in-flight message on both backends.
3. THE OPERATIONS.md delivery-guarantees subsection SHALL state that upgrading to at-least-once requires changing the ack/commit point on both bindings together.

### Requirement 7: Kafka Compose Service

**User Story:** As a developer, I want to start a Kafka broker alongside the
existing stack via Docker Compose, so that I can test the Kafka binding locally
without modifying the default compose behavior.

#### Acceptance Criteria

1. THE `deploy/docker-compose.yml` SHALL define a `kafka` service using image `bitnami/kafka:3.7` in KRaft mode (no Zookeeper).
2. THE `kafka` compose service SHALL be gated behind a compose profile named `kafka` so that `docker compose up` without `--profile kafka` starts the existing stack unchanged.
3. WHEN the `kafka` compose service is started, THE service SHALL expose port `9092` and advertise itself as `kafka:9092` to other containers on the compose network.
4. THE `kafka` compose service environment SHALL set `KAFKA_CFG_PROCESS_ROLES: controller,broker`, `KAFKA_CFG_NODE_ID: "0"`, and the listener/voter configuration required for single-node KRaft operation.

### Requirement 8: Kubernetes Helm Chart Structure

**User Story:** As an operator, I want a Helm chart that deploys all IntelliOps
platform services to a Kubernetes cluster with a single command, so that I can
reproduce the production stack on any kind cluster without writing bespoke manifests.

#### Acceptance Criteria

1. THE Helm_Chart SHALL be located at `deploy/k8s/platform/` and SHALL contain `Chart.yaml`, `values.yaml`, and a `templates/` directory.
2. THE Helm_Chart `templates/` directory SHALL contain: `_helpers.tpl`, `service-deployment.yaml`, `redis.yaml`, `postgres.yaml`, `migrate-job.yaml`, and `configmap.yaml`.
3. THE Helm_Chart `templates/service-deployment.yaml` SHALL range over `values.services` and render one Kubernetes `Deployment` and one `Service` manifest per entry.
4. WHEN rendering a service Deployment, THE `service-deployment.yaml` template SHALL set the `SERVICE_MODULE` environment variable from `service.module` and the `PORT` environment variable from `service.port`, and SHALL mount `intelliops-env` ConfigMap as `envFrom`.
5. THE `values.yaml` SHALL enumerate the following services, each with `name`, `module`, and `port` fields matching `deploy/docker-compose.yml` exactly: `ingestion` (module `services.ingestion.app:app`, port `8000`), `correlation` (module `services.correlation.app:app`, port `8000`), `rca` (module `services.rca.app:app`, port `8000`), `governance` (module `services.governance.app:app`, port `8000`), `action` (module `services.action.app:app`, port `8000`), `feedback` (module `services.feedback.app:app`, port `8000`), `read` (module `services.read.app:app`, port `8000`).
6. THE `values.yaml` SHALL define `env` defaults: `AUTH_MODE: "off"`, `STORE_BACKEND: postgres`, `BUS_BACKEND: redis`.
7. THE `templates/configmap.yaml` SHALL render a ConfigMap named `intelliops-env` populated from `values.env`.
8. THE `templates/postgres.yaml` SHALL render a `Deployment` and a `Service` for a Postgres instance using the image specified in `values.postgres.image` (default `postgres:16`).
9. THE `templates/redis.yaml` SHALL render a `Deployment` and a `Service` for a Redis instance using the image specified in `values.redis.image` (default `redis:7`).

### Requirement 9: Kubernetes Migrate Job

**User Story:** As an operator, I want database migrations to run automatically
before any application pods start during a Helm install or upgrade, so that I
never face a pod-starts-before-schema-exists race condition.

#### Acceptance Criteria

1. THE Migrate_Job SHALL be defined in `deploy/k8s/platform/templates/migrate-job.yaml` as a Kubernetes `Job` with Helm annotations `helm.sh/hook: pre-install,pre-upgrade` and `helm.sh/hook-delete-policy: before-hook-creation`.
2. THE Migrate_Job container SHALL run the command `alembic upgrade head`, matching the command used by the `migrate` service in `deploy/docker-compose.yml`.
3. THE Migrate_Job SHALL use the same `intelliops` image as the platform services and SHALL mount the `intelliops-env` ConfigMap as `envFrom`.
4. WHEN the Migrate_Job completes successfully, THE Helm_Chart install SHALL proceed to deploy application pods.

### Requirement 10: One-Command Kubernetes Deploy Documentation

**User Story:** As an operator, I want a single documented command to deploy the
entire IntelliOps stack to a kind cluster, so that I can reproduce the deployment
without referencing multiple disconnected sources.

#### Acceptance Criteria

1. THE OPERATIONS.md SHALL contain a "Kubernetes deploy" section with the exact command `helm install intelliops deploy/k8s/platform/` and prerequisites (kind cluster running, `intelliops` image built and loaded).
2. THE OPERATIONS.md SHALL contain a table listing every runtime environment switch (`AUTH_MODE`, `STORE_BACKEND`, `BUS_BACKEND`, `REMEDIATOR_MODE`) with their accepted values and defaults, without duplicating the existing auth table.

### Requirement 11: Load Testing Script

**User Story:** As a developer, I want a script that drives synthetic incidents
through the ingestion endpoint at a configurable rate and reports p50/p95 latency
and throughput numbers, so that I have concrete performance data to include in OPERATIONS.md.

#### Acceptance Criteria

1. THE Load_Test_Script SHALL be located at `scripts/load-test.sh` and SHALL accept two optional positional arguments: `incidents_per_minute` (default `60`) and `duration_seconds` (default `60`).
2. WHEN executed, THE Load_Test_Script SHALL POST `IngestBatch` payloads to `POST /ingest` on the Ingestion_Service at the configured rate for the configured duration.
3. THE `IngestBatch` payload generated by the Load_Test_Script SHALL conform to the schema `{"events": [{"source": <str>, "kind": "metric", "name": <str>, "value": <float>, "labels": {}, "ts": <ISO-8601 datetime>}]}`. The `fingerprint` field SHALL NOT be included in the client-supplied dict — it is computed server-side by `normalize()` via `compute_fingerprint(source, name, labels)` and any client-supplied value is silently ignored. Note: `IngestBatch.events` is typed as `list[dict]` with no schema validation at the HTTP boundary; malformed raw events produce a 500 (KeyError/ValueError inside `normalize()`), not a 4xx — the script should not assert on clean validation errors if it sends unexpected shapes.
4. WHEN the test completes, THE Load_Test_Script SHALL report to stdout: total events sent, p50 end-to-end latency in milliseconds, and p95 end-to-end latency in milliseconds.
5. WHEN the test completes, THE Load_Test_Script SHALL append a timestamped results block to `docs/OPERATIONS.md` containing the same metrics reported to stdout.
6. THE Load_Test_Script SHALL be executable (`chmod +x`) and SHALL include a usage comment at the top of the file.

### Requirement 12: OPERATIONS.md Completeness

**User Story:** As an operator, I want OPERATIONS.md to be the single reference for
running and understanding the IntelliOps platform, covering all env switches,
delivery semantics, load test results, and deployment instructions.

#### Acceptance Criteria

1. THE OPERATIONS.md SHALL contain a single consolidated env-switch reference table covering `AUTH_MODE`, `STORE_BACKEND`, `BUS_BACKEND`, and `REMEDIATOR_MODE`, with accepted values, defaults, and a brief description for each switch.
2. THE OPERATIONS.md env-switch table SHALL NOT duplicate the existing auth section's `AUTH_MODE` rows; THE OPERATIONS.md SHALL cross-reference the existing auth section instead of repeating it.
3. WHEN a load test has been run, THE OPERATIONS.md SHALL contain a load test results block with at minimum: the date/time of the run, incidents-per-minute rate used, total events sent, p50 latency, and p95 latency.
