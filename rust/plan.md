# Rust migration plan: intelliops (Python)

1 package, 118 source files (10145 lines), 152 test files, 0 UI files, grouped into 26 units.

| Ready | Caution | Blocked | Keep in source language |
| ---: | ---: | ---: | ---: |
| 12 | 2 | 12 | 0 |

## Start here

Best first ports: safe to port (readiness) and worth porting (loops and string work, tests, size).

| # | Unit | Lines | Priority | Readiness | Tests | Bridge | Main risks |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 1 | `services/feedback/adapters/` | 57 | 79 | 100 | 6 | PyO3 | Dynamic typing (2) |
| 2 | `services/correlation/benchmark/` | 344 | 78 | 93 | 1 | PyO3 | Dynamic typing (1); Division and modulo (1); Clock and random numbers (5) |
| 3 | `services/rca/adapters/` | 268 | 72 | 88 | 7 | PyO3 | Dynamic typing (7); Exceptions (5); Number semantics (3) |
| 4 | `rust/harness/` | 247 | 72 | 70 | 0 | PyO3 | String offsets and slicing (7); Division and modulo (4); Dynamic typing (1) |
| 5 | `alembic/versions/` | 204 | 67 | 99 | 0 | PyO3 | - |
| 6 | `alembic/` | 52 | 64 | 100 | 0 | PyO3 | - |
| 7 | `scripts/` | 357 | 59 | 75 | 0 | PyO3 | String offsets and slicing (1); Hash iteration order (1); Dynamic typing (7) |
| 8 | `services/action/adapters/` | 758 | 57 | 63 | 14 | PyO3 | String offsets and slicing (1); Hash iteration order (1); Dynamic typing (26) |
| 9 | `services/correlation/adapters/` | 633 | 52 | 52 | 19 | PyO3 | String offsets and slicing (1); Hash iteration order (1); Dynamic typing (4) |

### `services/feedback/adapters/`

57 lines in 2 files, wave 0, priority 79, readiness 100, bridge **PyO3**.

- Import cycle with services/governance/adapters/, services/, services/correlation/, services/correlation/adapters/, common/: port them together.
- Imports `common/`: port it first, or port them together.
- 6 test files import it directly.
- Dynamic typing x2: `services/feedback/adapters/training_store.py:53`, `services/feedback/adapters/training_store.py:57`

### `services/correlation/benchmark/`

344 lines in 3 files, wave 1, priority 78, readiness 93, bridge **PyO3**.

- Imports `common/`, `services/correlation/adapters/`: port them first, or port them together.
- 1 test file imports it directly.
- Dynamic typing x1: `services/correlation/benchmark/runner.py:157`
- Division and modulo x1: `services/correlation/benchmark/scenarios.py:136`
- Clock and random numbers x5: `services/correlation/benchmark/scenarios.py:73`, `services/correlation/benchmark/scenarios.py:101`, `services/correlation/benchmark/scenarios.py:131`
- Number semantics x3: `services/correlation/benchmark/runner.py:44`, `services/correlation/benchmark/runner.py:67`, `services/correlation/benchmark/runner.py:79`

### `services/rca/adapters/`

268 lines in 4 files, wave 1, priority 72, readiness 88, bridge **PyO3**.

- 1 PyPI dependency not in the crate table: check it by hand.
- Imports `common/`: port it first, or port them together.
- 7 test files import it directly.
- Dynamic typing x7: `services/rca/adapters/context_provider.py:29`, `services/rca/adapters/explanation_provider.py:151`, `services/rca/adapters/runbook_selector.py:32`
- Exceptions x5: `services/rca/adapters/explanation_provider.py:112`, `services/rca/adapters/explanation_provider.py:128`, `services/rca/adapters/explanation_provider.py:136`
- Number semantics x3: `services/rca/adapters/explanation_provider.py:64`, `services/rca/adapters/explanation_provider.py:153`, `services/rca/adapters/runbook_selector.py:77`

### `rust/harness/`

247 lines in 2 files, wave 0, priority 72, readiness 70, bridge **PyO3**.

- 1 PyPI dependency not in the crate table: check it by hand.
- Leaf: imports no other unit.
- String offsets and slicing x7: `rust/harness/capture.py:90`, `rust/harness/measure.py:70`, `rust/harness/measure.py:76`
- Division and modulo x4: `rust/harness/measure.py:91`, `rust/harness/measure.py:118`, `rust/harness/measure.py:120`
- Dynamic typing x1: `rust/harness/measure.py:100`
- Exceptions x4: `rust/harness/capture.py:70`, `rust/harness/measure.py:51`, `rust/harness/measure.py:56`
- Number semantics x3: `rust/harness/capture.py:54`, `rust/harness/measure.py:41`, `rust/harness/measure.py:95`
- Clock and random numbers x2: `rust/harness/measure.py:113`, `rust/harness/measure.py:156`

### `alembic/versions/`

204 lines in 7 files, wave 0, priority 67, readiness 99, bridge **PyO3**.

- 7 imports use aliases or virtual modules and were not followed.
- Leaf: imports no other unit.

## Order by package

Packages come after the workspace packages they use. Inside a package, port by wave: a unit's imports are in earlier waves.

### 1. intelliops (`.`, portable, 152 test files)

| Wave | Unit | Status | Lines | Readiness | Bridge | Main risks |
| ---: | --- | --- | ---: | ---: | --- | --- |
| 0 | `services/feedback/adapters/` | ready | 57 | 100 | PyO3 | Dynamic typing (2) |
| 0 | `alembic/versions/` | ready | 204 | 99 | PyO3 | - |
| 0 | `rust/harness/` | ready | 247 | 70 | PyO3 | String offsets and slicing (7); Division and modulo (4) |
| 0 | `services/` | blocked | 141 | 66 | PyO3 | Async and promises (10); Dynamic typing (3) |
| 0 | `services/correlation/` | blocked | 501 | 57 | PyO3 | Hash iteration order (1); Dynamic typing (28) |
| 0 | `services/correlation/adapters/` | ready | 633 | 52 | PyO3 | String offsets and slicing (1); Hash iteration order (1) |
| 0 | `services/governance/adapters/` | caution | 1517 | 31 | PyO3 | String offsets and slicing (9); Hash iteration order (2) |
| 0 | `common/` | blocked | 1373 | 0 | PyO3 | Hash iteration order (5); String offsets and slicing (2) |
| 1 | `alembic/` | ready | 52 | 100 | PyO3 | - |
| 1 | `services/ingestion/adapters/` | caution | 91 | 96 | PyO3 | Generators (4); Dynamic typing (2) |
| 1 | `services/correlation/benchmark/` | ready | 344 | 93 | PyO3 | Dynamic typing (1); Division and modulo (1) |
| 1 | `services/demo_app/` | blocked | 104 | 89 | PyO3 | Dynamic typing (1); Exceptions (1) |
| 1 | `services/rca/adapters/` | ready | 268 | 88 | PyO3 | Dynamic typing (7); Exceptions (5) |
| 1 | `services/ingestion/` | blocked | 95 | 75 | PyO3 | String offsets and slicing (1); Dynamic typing (2) |
| 1 | `services/feedback/` | blocked | 220 | 74 | PyO3 | Hash iteration order (3); Dynamic typing (3) |
| 1 | `services/meridian/` | blocked | 321 | 68 | PyO3 | Async and promises (5); Dynamic typing (2) |
| 1 | `services/read/` | blocked | 784 | 31 | PyO3 | Hash iteration order (4); String offsets and slicing (3) |
| 1 | `services/governance/` | blocked | 701 | 25 | PyO3 | Hash iteration order (4); String offsets and slicing (3) |
| 2 | `services/meridian/aggregation/` | ready | 12 | 100 | PyO3 | Dynamic typing (1) |
| 2 | `services/meridian/reporting/` | ready | 12 | 100 | PyO3 | Dynamic typing (1) |
| 2 | `services/meridian/validation/` | ready | 14 | 100 | PyO3 | Dynamic typing (1) |
| 2 | `services/meridian/gateway/` | blocked | 162 | 81 | PyO3 | Hash iteration order (2); Dynamic typing (3) |
| 2 | `scripts/` | ready | 357 | 75 | PyO3 | String offsets and slicing (1); Hash iteration order (1) |
| 2 | `services/action/adapters/` | ready | 758 | 63 | PyO3 | String offsets and slicing (1); Hash iteration order (1) |
| 2 | `services/rca/` | blocked | 507 | 54 | PyO3 | String offsets and slicing (2); Hash iteration order (1) |
| 2 | `services/action/` | blocked | 670 | 47 | PyO3 | String offsets and slicing (7); Dynamic typing (14) |

## PyPI dependencies

| Package | Units | Rust | Fit | Note |
| --- | ---: | --- | --- | --- |
| `fastapi` | 12 | - | none | Web framework: keep the app in Python. |
| `prometheus-client` | 3 | ? | unknown |  |
| `joblib` | 1 | ? | unknown |  |
| `kafka` | 1 | ? | unknown |  |
| `kubernetes` | 1 | ? | unknown |  |
| `measure` | 1 | ? | unknown |  |
| `pydantic-settings` | 1 | ? | unknown |  |
| `river` | 1 | ? | unknown |  |
| `sentence-transformers` | 1 | ? | unknown |  |
| `sqlalchemy` | 10 | sqlx | partial | ORM layers port by hand. |
| `pydantic` | 6 | serde, garde | partial | Validation errors will differ. |
| `numpy` | 2 | ndarray | partial | Broadcasting and dtype rules differ; check numerics. |
| `yaml` | 2 | serde_yaml_ng, saphyr | partial | Round-tripping comments is not covered. |
| `sklearn` | 1 | linfa | partial | estimators have to be retrained or the model loaded |
| `httpx` | 8 | reqwest | good |  |
| `redis` | 2 | redis | good |  |

## Blocked

- `common/`: `fastapi` has no Rust equivalent.
- `services/`: `fastapi` has no Rust equivalent.
- `services/action/`: `fastapi` has no Rust equivalent.
- `services/correlation/`: `fastapi` has no Rust equivalent.
- `services/demo_app/`: `fastapi` has no Rust equivalent.
- `services/feedback/`: `fastapi` has no Rust equivalent.
- `services/governance/`: `fastapi` has no Rust equivalent.
- `services/ingestion/`: `fastapi` has no Rust equivalent.
- `services/meridian/`: `fastapi` has no Rust equivalent.
- `services/meridian/gateway/`: `fastapi` has no Rust equivalent.
- `services/rca/`: `fastapi` has no Rust equivalent.
- `services/read/`: `fastapi` has no Rust equivalent.

## Risk guide

- **String offsets and slicing** (High): Rust slices strings at UTF-8 byte offsets that fall on character boundaries. Convert offsets from the source's indexing units as needed and test with non-ASCII text.
- **Dynamic typing** (Medium): Values whose shape is checked at runtime need an explicit Rust enum or `serde_json::Value`.
- **Exceptions** (Low): Represent source exceptions with `Result`. Preserve the original exception type and message when errors cross the bridge.
- **Async and promises** (Medium): Promises become futures. Bridge with napi-rs `async fn` or `wasm-bindgen-futures`, or keep the async shell in JS and port the sync core.
- **Generators** (Medium): Lazy generators become iterators, or return a collected Vec across the bridge.
- **Class inheritance** (Medium): Class hierarchies become traits plus composition; `instanceof` checks become enums.
- **Reflection and prototypes** (Medium): Proxy, Reflect, Symbol and prototype tricks need a redesign, not a translation.
- **Number semantics** (Low): JS numbers are f64; bitwise ops truncate to i32; parseInt is lenient. Pick Rust integer types deliberately. Printing differs: `${n}` gives `1e+21`, `1e-7` and `0` for -0 where Rust's `{}` does not, so use `js::number_to_string`. `Math.round` rounds halves up (-2.5 gives -2), Rust's `round` away from zero: use `js::math_round`. `toFixed` rounds exact halves up ((2.5).toFixed(0) is "3"), Rust's `{:.0}` to even.
- **Integer width and overflow** (Medium): Python ints never overflow; C, Go and Java ints wrap or are undefined on overflow. Pick i64, i128 or a bignum crate deliberately, and use wrapping_* where the source wraps.
- **Division and modulo** (Medium): Python `//` and `%` floor toward negative infinity; Rust `/` and `%` truncate: use `div_euclid`/`rem_euclid` or explicit floor math. C division by zero is undefined.
- **Hash iteration order** (High): Set and map iteration order comes from the source language's hash function. A port cannot reproduce it: sort before output, or make the order part of the contract.
- **Concurrency** (Medium): Threads, goroutines and locks need a design in Rust (channels, Send/Sync), not a line-by-line translation; keep concurrency in the caller where possible.
- **Clock and random numbers** (Low): Results that depend on the time or on random numbers differ on every run, so a plain recording cannot pin them down. Record with `record --pin-clock` (TS/JS with the napi bridge, Python): `Date.now()`, `new Date()` and `Math.random` (`time.time`, `datetime.now` and `random`) then return fixed values during record, diff and gate, and the port reads the same ones from `src/port/pinned.rs` (`now_ms()`, `random()`, a documented mulberry32). Or pass the time and the seed in as parameters.

## Whole app

Shape: **server**.

| Entry point | Kind | Found from |
| --- | --- | --- |
| `rust/harness/capture.py` | cli | `if __name__ == "__main__"` at rust/harness/capture.py:121 |
| `rust/harness/measure.py` | cli | `if __name__ == "__main__"` at rust/harness/measure.py:170 |
| `scripts/benchmark.py` | cli | `if __name__ == "__main__"` at scripts/benchmark.py:90 |
| `scripts/delivery_probe.py` | cli | `if __name__ == "__main__"` at scripts/delivery_probe.py:115 |
| `services/base.py` | server | `FastAPI(` at services/base.py:75 |
| `services/demo_app/app.py` | server | `FastAPI(` at services/demo_app/app.py:28 |

### Every dependency

| Dependency | Declared | Used by | Rust | Fit | Note |
| --- | --- | --- | --- | --- | --- |
| `fastapi` | yes | 12 | - | none | Web framework: keep the app in Python. |
| `uvicorn` | yes | 0 | axum | none | a server is an application rewrite, not a port |
| `prometheus-client` | yes | 3 | ? | unknown |  |
| `joblib` | no | 1 | ? | unknown |  |
| `kafka` | no | 1 | ? | unknown |  |
| `kubernetes` | no | 1 | ? | unknown |  |
| `measure` | no | 1 | ? | unknown |  |
| `pydantic-settings` | yes | 1 | ? | unknown |  |
| `river` | no | 1 | ? | unknown |  |
| `sentence-transformers` | no | 1 | ? | unknown |  |
| `alembic` | yes | 0 | ? | unknown |  |
| `kafka-python` | yes | 0 | ? | unknown |  |
| `psycopg` (also `sqlalchemy`) | yes | 10 | sqlx | partial | ORM layers port by hand. |
| `pydantic` | yes | 6 | serde, garde | partial | Validation errors will differ. |
| `numpy` | no | 2 | ndarray | partial | Broadcasting and dtype rules differ; check numerics. |
| `pyyaml` (also `yaml`) | yes | 2 | serde_yaml_ng, saphyr | partial | Round-tripping comments is not covered. |
| `sklearn` | no | 1 | linfa | partial | estimators have to be retrained or the model loaded |
| `httpx` | yes | 8 | reqwest | good |  |
| `redis` | yes | 2 | redis | good |  |

### Pieces, in port order

- wave 0: `alembic/versions/` (204 lines)
- wave 0: `common/`, `services/`, `services/correlation/`, `services/correlation/adapters/`, `services/feedback/adapters/`, `services/governance/adapters/`, tangled: one piece (4222 lines)
- wave 0: `rust/harness/` (247 lines)
- wave 1: `alembic/` (52 lines)
- wave 1: `services/correlation/benchmark/` (344 lines)
- wave 1: `services/demo_app/` (104 lines)
- wave 1: `services/feedback/` (220 lines)
- wave 1: `services/governance/` (701 lines)
- wave 1: `services/ingestion/`, `services/ingestion/adapters/`, tangled: one piece (186 lines)
- wave 1: `services/meridian/` (321 lines)
- wave 1: `services/rca/adapters/` (268 lines)
- wave 1: `services/read/` (784 lines)
- wave 2: `scripts/` (357 lines)
- wave 2: `services/action/`, `services/action/adapters/`, tangled: one piece (1428 lines)
- wave 2: `services/meridian/aggregation/` (12 lines)
- wave 2: `services/meridian/gateway/` (162 lines)
- wave 2: `services/meridian/reporting/` (12 lines)
- wave 2: `services/meridian/validation/` (14 lines)
- wave 2: `services/rca/` (507 lines)

### Decisions for the user

**D1** (shape): The app is a server, entered through rust/harness/capture.py, rust/harness/measure.py, scripts/benchmark.py, scripts/delivery_probe.py, services/base.py, services/demo_app/app.py. Port every entry point, in that shape?

> Yes: an axum front door (`app front-door`) takes the server over first

**D2** (blocked common/): common/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D3** (blocked services/): services/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D4** (blocked services/action/): services/action/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D5** (blocked services/correlation/): services/correlation/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D6** (blocked services/demo_app/): services/demo_app/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D7** (blocked services/feedback/): services/feedback/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D8** (blocked services/governance/): services/governance/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D9** (blocked services/ingestion/): services/ingestion/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D10** (blocked services/meridian/): services/meridian/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D11** (blocked services/meridian/gateway/): services/meridian/gateway/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D12** (blocked services/rca/): services/rca/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D13** (blocked services/read/): services/read/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D14** (dependency fastapi): No crate does what fastapi does (used by 12 units; Web framework: keep the app in Python). Reimplement what the app uses, or drop the feature?

> Reimplement only the part the app uses, proved by its recorded cases

**D15** (dependency uvicorn): No crate does what uvicorn does (declared, no unit imports it; a server is an application rewrite, not a port). Reimplement what the app uses, or drop the feature?

> Reimplement only the part the app uses, proved by its recorded cases

**D16** (dependency prometheus-client): Which crate replaces prometheus-client (used by 3 units)?

> Research it against primary sources into rust/research/prometheus-client.md before porting what uses it

**D17** (dependency joblib): Which crate replaces joblib (used by 1 unit)?

> Research it against primary sources into rust/research/joblib.md before porting what uses it

**D18** (dependency kafka): Which crate replaces kafka (used by 1 unit)?

> Research it against primary sources into rust/research/kafka.md before porting what uses it

**D19** (dependency kubernetes): Which crate replaces kubernetes (used by 1 unit)?

> Research it against primary sources into rust/research/kubernetes.md before porting what uses it

**D20** (dependency measure): Which crate replaces measure (used by 1 unit)?

> Research it against primary sources into rust/research/measure.md before porting what uses it

**D21** (dependency pydantic-settings): Which crate replaces pydantic-settings (used by 1 unit)?

> Research it against primary sources into rust/research/pydantic-settings.md before porting what uses it

**D22** (dependency river): Which crate replaces river (used by 1 unit)?

> Research it against primary sources into rust/research/river.md before porting what uses it

**D23** (dependency sentence-transformers): Which crate replaces sentence-transformers (used by 1 unit)?

> Research it against primary sources into rust/research/sentence-transformers.md before porting what uses it

**D24** (dependency alembic): Which crate replaces alembic (declared, no unit imports it)?

> Research it against primary sources into rust/research/alembic.md before porting what uses it

**D25** (dependency kafka-python): Which crate replaces kafka-python (declared, no unit imports it)?

> Research it against primary sources into rust/research/kafka-python.md before porting what uses it

**D26** (dependency psycopg): psycopg maps to sqlx only in part (used by 10 units; ORM layers port by hand). Use it?

> Use sqlx, and record cases for the behavior it does not cover

**D27** (dependency pydantic): pydantic maps to serde, garde only in part (used by 6 units; Validation errors will differ). Use it?

> Use serde, garde, and record cases for the behavior it does not cover

**D28** (dependency numpy): numpy maps to ndarray only in part (used by 2 units; Broadcasting and dtype rules differ; check numerics). Use it?

> Use ndarray, and record cases for the behavior it does not cover

**D29** (dependency pyyaml): pyyaml maps to serde_yaml_ng, saphyr only in part (used by 2 units; Round-tripping comments is not covered). Use it?

> Use serde_yaml_ng, saphyr, and record cases for the behavior it does not cover

**D30** (dependency sklearn): sklearn maps to linfa only in part (used by 1 unit; estimators have to be retrained or the model loaded). Use it?

> Use linfa, and record cases for the behavior it does not cover

**D31** (tangled units): common/, services/, services/correlation/, services/correlation/adapters/, services/feedback/adapters/, services/governance/adapters/ import each other (4222 lines). Port them as one piece?

> Yes: one piece in wave 0, no glue between its parts

**D32** (tangled units): services/ingestion/, services/ingestion/adapters/ import each other (186 lines). Port them as one piece?

> Yes: one piece in wave 1, no glue between its parts

**D33** (tangled units): services/action/, services/action/adapters/ import each other (1428 lines). Port them as one piece?

> Yes: one piece in wave 2, no glue between its parts

