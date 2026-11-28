//! The five apps. Controls (`/break`, `/admin/fault`) need the token in token
//! auth mode; `/metrics` and the probes are always open.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use axum::extract::{Request, State as Axum};
use axum::http::{StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use intelliops_service::Config;
use serde_json::{Value, json};
use subtle::ConstantTimeEq;
use tower_http::services::ServeDir;

use crate::metrics::{CONTENT_TYPE, family, render};
use crate::state::{Fault, State};

const MERIDIAN: [&str; 4] = ["gateway", "validation", "aggregation", "reporting"];
const KNOWN_METRICS: &str = "cpu_usage|meridian_error_rate|request_rate|latency_p50_ms|\
                             latency_p99_ms|memory_usage_mb|saturation|queue_depth|\
                             db_pool_in_use|db_pool_max|disk_usage_percent";
const PEER_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct Sim {
    name: Arc<str>,
    inner: Arc<Mutex<Inner>>,
    config: Config,
    jitter: bool,
    client: reqwest::Client,
}

#[derive(Default)]
struct Inner {
    state: State,
    broken: bool,
    requests: u64,
    errors: u64,
}

impl Sim {
    #[must_use]
    pub fn new(name: &str, config: Config) -> Self {
        let jitter = matches!(
            config
                .get("telemetry_jitter", "0")
                .to_ascii_lowercase()
                .as_str(),
            "1" | "true" | "yes" | "on"
        );
        Self {
            name: name.into(),
            inner: Arc::default(),
            config,
            jitter,
            client: reqwest::Client::new(),
        }
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn wander(&self) -> Option<&str> {
        self.jitter.then_some(&*self.name)
    }
}

async fn require_token(Axum(sim): Axum<Sim>, request: Request, next: Next) -> Response {
    if sim.config.get("auth_mode", "off") == "token" {
        let expected = sim.config.get("auth_token", "");
        let given = request
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .unwrap_or("")
            .trim();
        if expected.is_empty() || !bool::from(given.as_bytes().ct_eq(expected.as_bytes())) {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({"detail": "Unauthorized"})),
            )
                .into_response();
        }
    }
    next.run(request).await
}

fn controls(sim: &Sim, routes: Router<Sim>) -> Router<Sim> {
    routes.layer(middleware::from_fn_with_state(sim.clone(), require_token))
}

fn probes(sim: &Sim) -> Router<Sim> {
    let name = sim.name.clone();
    Router::new()
        .route(
            "/health",
            get(move || async move { Json(json!({"service": &*name, "status": "ok"})) }),
        )
        .route("/ready", get(|| async { Json(json!({"ready": true})) }))
}

fn exposition(body: String) -> Response {
    ([(header::CONTENT_TYPE, CONTENT_TYPE)], body).into_response()
}

// ---- demo app --------------------------------------------------------------

fn set_broken(sim: &Sim, broken: bool) -> Json<Value> {
    let mut inner = sim.lock();
    inner.broken = broken;
    let mut state = State::default();
    if broken {
        state.cpu = 92.0;
        state.latency_p50_ms = 220.0;
        state.latency_p99_ms = 950.0;
        state.memory_usage_mb = 700.0;
        state.saturation = 0.9;
        state.queue_depth = 180.0;
    }
    inner.state = state;
    Json(json!({"broken": broken}))
}

async fn work(Axum(sim): Axum<Sim>) -> Response {
    let mut inner = sim.lock();
    inner.requests += 1;
    if inner.broken {
        inner.errors += 1;
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail": "dependency failure"})),
        )
            .into_response();
    }
    Json(json!({"result": "ok"})).into_response()
}

async fn demo_metrics(Axum(sim): Axum<Sim>) -> Response {
    let inner = sim.lock();
    // The demo app is a single endpoint: no availability, TLS or error-ratio gauge.
    let skip = [
        "service_up",
        "tls_handshake_failures",
        "meridian_error_rate",
    ];
    let mut body = render(&inner.state, sim.wander(), &skip);
    family(
        &mut body,
        "http_requests_total",
        "counter",
        "Total work requests",
        inner.requests as f64,
    );
    family(
        &mut body,
        "http_request_errors_total",
        "counter",
        "Total failed work requests",
        inner.errors as f64,
    );
    exposition(body)
}

/// A single breakable target.
pub fn demo_app(sim: Sim) -> Router {
    let toggles = Router::new()
        .route(
            "/break",
            post(|Axum(sim): Axum<Sim>| async move { set_broken(&sim, true) }),
        )
        .route(
            "/fix",
            post(|Axum(sim): Axum<Sim>| async move { set_broken(&sim, false) }),
        );
    probes(&sim)
        .route("/work", get(work))
        .route("/metrics", get(demo_metrics))
        .merge(controls(&sim, toggles))
        .with_state(sim)
}

// ---- Meridian --------------------------------------------------------------

async fn meridian_metrics(Axum(sim): Axum<Sim>) -> Response {
    let mut inner = sim.lock();
    inner.state.sample();
    exposition(render(&inner.state, sim.wander(), &[]))
}

async fn fault(Axum(sim): Axum<Sim>, Json(fault): Json<Fault>) -> Json<Value> {
    let mut inner = sim.lock();
    inner.state.apply(&fault);
    Json(json!({"applied": fault.kind, "cpu": inner.state.cpu,
        "error_rate": inner.state.error_rate}))
}

async fn clear(Axum(sim): Axum<Sim>) -> Json<Value> {
    sim.lock().state.clear();
    Json(json!({"cleared": true}))
}

/// A latency fault slows real requests, never probes or controls.
async fn delay(Axum(sim): Axum<Sim>, request: Request, next: Next) -> Response {
    let millis = sim.lock().state.latency_ms;
    if millis > 0.0
        && let Ok(pause) = Duration::try_from_secs_f64(millis.min(30_000.0) / 1000.0)
    {
        tokio::time::sleep(pause).await;
    }
    next.run(request).await
}

fn text(body: &Value, key: &str) -> String {
    body[key].as_str().unwrap_or("").to_string()
}

fn domain(name: &str) -> Router<Sim> {
    match name {
        "meridian-validation" => Router::new().route(
            "/validate",
            post(|Json(body): Json<Value>| async move {
                let (client, period) = (text(&body, "client"), text(&body, "period"));
                let valid = !client.is_empty() && !period.is_empty();
                Json(json!({"valid": valid, "client": client, "period": period}))
            }),
        ),
        "meridian-aggregation" => Router::new().route(
            "/aggregate",
            post(|Json(body): Json<Value>| async move {
                let rows = body["rows"].as_array().map_or(0, Vec::len);
                Json(json!({"aggregated": true, "rows": rows}))
            }),
        ),
        "meridian-reporting" => Router::new().route(
            "/report",
            post(|Json(body): Json<Value>| async move {
                Json(json!({"generated": true,
                    "submission_id": body["submission_id"], "summary": "ok"}))
            }),
        ),
        _ => Router::new()
            .route(
                "/api/submissions",
                post(|Json(body): Json<Value>| async move {
                    Json(json!({"accepted": true, "client": text(&body, "client"),
                        "period": text(&body, "period"),
                        "amount": body["amount"].as_f64().unwrap_or(0.0)}))
                }),
            )
            .route(
                "/api/reports",
                get(|| async { Json(json!({"reports": []})) }),
            ),
    }
}

// ---- gateway ops proxy ------------------------------------------------------

fn detail(status: StatusCode, message: String) -> Response {
    (status, Json(json!({"detail": message}))).into_response()
}

/// The name is put into a URL, so only the four known services pass.
fn known(body: &Value) -> Result<&'static str, String> {
    let name = body["service"].as_str().unwrap_or("");
    MERIDIAN
        .into_iter()
        .find(|s| *s == name)
        .ok_or_else(|| format!("Unknown Meridian service: {name:?}"))
}

fn target(sim: &Sim, service: &str, path: &str) -> String {
    if sim.config.get("meridian_ops_target_mode", "compose") == "k8s" {
        let port = match service {
            "gateway" => 30808,
            "validation" => 30811,
            "aggregation" => 30812,
            _ => 30813,
        };
        format!("http://host.docker.internal:{port}/{path}")
    } else {
        format!("http://meridian-{service}:8000/{path}")
    }
}

/// Server side, so the browser never holds the admin token.
async fn forward(sim: &Sim, url: String, body: Option<&Value>) -> Response {
    let mut request = sim.client.post(url).timeout(PEER_TIMEOUT);
    let token = sim.config.get("auth_token", "");
    if !token.is_empty() {
        request = request.bearer_auth(token);
    }
    if let Some(body) = body {
        request = request.json(body);
    }
    match request.send().await {
        Ok(response) => Json(json!({"status": response.status().as_u16()})).into_response(),
        Err(error) => {
            tracing::warn!(%error, "ops target unreachable");
            detail(StatusCode::BAD_GATEWAY, "target unreachable".to_string())
        }
    }
}

async fn ops_fault(Axum(sim): Axum<Sim>, Json(body): Json<Value>) -> Response {
    match known(&body) {
        Ok(service) => {
            forward(
                &sim,
                target(&sim, service, "admin/fault"),
                Some(&body["spec"]),
            )
            .await
        }
        Err(reason) => detail(StatusCode::BAD_REQUEST, reason),
    }
}

async fn ops_clear(Axum(sim): Axum<Sim>, Json(body): Json<Value>) -> Response {
    match known(&body) {
        Ok(service) => forward(&sim, target(&sim, service, "admin/clear"), None).await,
        Err(reason) => detail(StatusCode::BAD_REQUEST, reason),
    }
}

/// Write the deploy marker RCA reads, for the rollback scenario.
async fn ops_deploy(Axum(sim): Axum<Sim>, Json(body): Json<Value>) -> Response {
    let service = match known(&body) {
        Ok(service) => format!("meridian-{service}"),
        Err(reason) => return detail(StatusCode::BAD_REQUEST, reason),
    };
    let dir = PathBuf::from(sim.config.get("rca_context_path", "data/rca_context"));
    let entry = json!([{
        "service": service,
        "version": body["version"].as_str().unwrap_or("v2.3.1"),
        "ts": intelliops_service::now_rfc3339(),
    }]);
    let written = std::fs::create_dir_all(&dir)
        .and_then(|()| std::fs::write(dir.join("deploys.json"), entry.to_string()));
    match written {
        Ok(()) => Json(json!({"deployed": service})).into_response(),
        Err(error) => {
            tracing::error!(%error, "could not write the deploy marker");
            detail(
                StatusCode::INTERNAL_SERVER_ERROR,
                "could not record the deploy".to_string(),
            )
        }
    }
}

/// Live telemetry through the gateway, so the UI stays same-origin. Any
/// Prometheus problem gives an empty payload.
async fn ops_metrics(Axum(sim): Axum<Sim>) -> Json<Value> {
    let url = format!(
        "{}/api/v1/query",
        sim.config
            .get("prometheus_url", "http://localhost:9090")
            .trim_end_matches('/')
    );
    let query = format!("{{__name__=~\"{KNOWN_METRICS}\"}}");
    let body: Option<Value> = async {
        let response = sim
            .client
            .get(url)
            .query(&[("query", query)])
            .timeout(PEER_TIMEOUT)
            .send()
            .await
            .ok()?
            .error_for_status()
            .ok()?;
        response.json().await.ok()
    }
    .await;
    Json(fold(body.as_ref()))
}

/// One row per service from a flat instant vector.
fn fold(body: Option<&Value>) -> Value {
    let mut rows: BTreeMap<String, serde_json::Map<String, Value>> = BTreeMap::new();
    let results = body
        .filter(|b| b["status"] == "success")
        .and_then(|b| b["data"]["result"].as_array());
    for entry in results.into_iter().flatten() {
        let (Some(service), Some(name)) = (
            entry["metric"]["service"].as_str(),
            entry["metric"]["__name__"].as_str(),
        ) else {
            continue;
        };
        let value = entry["value"][1]
            .as_str()
            .and_then(|v| v.parse::<f64>().ok());
        let Some(value) = value else { continue };
        if !KNOWN_METRICS.split('|').any(|known| known == name) {
            continue;
        }
        let row = rows.entry(service.to_string()).or_insert_with(|| {
            let mut row = serde_json::Map::new();
            row.insert("service".into(), service.into());
            row.insert("cpu_usage".into(), Value::Null);
            row.insert("error_rate".into(), Value::Null);
            row
        });
        row.insert(name.to_string(), value.into());
        if name == "meridian_error_rate" {
            row.insert("error_rate".into(), value.into());
        }
    }
    let services: Vec<Value> = rows
        .into_values()
        .map(|mut row| {
            let below = |key: &str, limit: f64| {
                row.get(key)
                    .and_then(Value::as_f64)
                    .is_none_or(|v| v < limit)
            };
            let healthy = below("cpu_usage", 50.0) && below("error_rate", 0.1);
            row.insert("healthy".into(), healthy.into());
            Value::Object(row)
        })
        .collect();
    json!({"scraped": !services.is_empty(), "services": services})
}

/// One of the four Meridian services. The gateway also serves the ops panel.
pub fn meridian(sim: Sim, ui: Option<&Path>) -> Router {
    let admin = Router::new()
        .route("/admin/fault", post(fault))
        .route("/admin/clear", post(clear));
    let name = sim.name.clone();
    let slowed = domain(&name).layer(middleware::from_fn_with_state(sim.clone(), delay));
    let mut routes = probes(&sim)
        .route("/metrics", get(meridian_metrics))
        .merge(controls(&sim, admin))
        .merge(slowed);
    if &*name == "meridian-gateway" {
        routes = routes
            .route("/api/ops/fault", post(ops_fault))
            .route("/api/ops/clear", post(ops_clear))
            .route("/api/ops/deploy", post(ops_deploy))
            .route("/api/ops/metrics", get(ops_metrics));
    }
    let routes = routes.with_state(sim);
    match ui.filter(|dir| dir.is_dir()) {
        Some(dir) => routes.fallback_service(ServeDir::new(dir)),
        None => routes,
    }
}
