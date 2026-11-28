//! Probes, posture, auth, CORS and request metrics around a service's routes.

use std::sync::Arc;
use std::time::Instant;

use axum::extract::{MatchedPath, Request, State};
use axum::http::{HeaderValue, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use intelliops_bus::Bus;
use intelliops_store::Store;
use prometheus_client::encoding::{EncodeLabelSet, text::encode};
use prometheus_client::metrics::counter::Counter;
use prometheus_client::metrics::family::Family;
use prometheus_client::metrics::histogram::Histogram;
use prometheus_client::registry::Registry;
use serde_json::{Map, Value, json};
use subtle::ConstantTimeEq;
use tower_http::cors::{AllowOrigin, Any, CorsLayer};

use crate::Config;

/// Probes and the scrape are open in every auth mode.
const PROBE_PATHS: [&str; 3] = ["/health", "/ready", "/_metrics"];

/// The posture each process reports about itself: modes only, never a secret.
const POSTURE: [(&str, &str); 11] = [
    ("store_backend", "file"),
    ("bus_backend", "redis"),
    ("auth_mode", "off"),
    ("correlator_kind", "river"),
    ("detection_policy", "off"),
    ("remediator_mode", "dry_run"),
    ("health_check_mode", "always"),
    ("sandbox_mode", "off"),
    ("runbook_selector_mode", "off"),
    ("bus_delivery", "at_most_once"),
    ("correlation_group_by", "window"),
];

const DURATION_BUCKETS: [f64; 14] = [
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0,
];

#[derive(Debug, Clone, Hash, PartialEq, Eq, EncodeLabelSet)]
struct Route {
    service: String,
    method: String,
    path: String,
}

#[derive(Debug, Clone, Hash, PartialEq, Eq, EncodeLabelSet)]
struct Handled {
    service: String,
    method: String,
    path: String,
    status: u16,
}

pub struct Service {
    name: &'static str,
    config: Config,
    bus: Bus,
    database: Option<Store>,
    registry: Registry,
    requests: Family<Handled, Counter>,
    duration: Family<Route, Histogram>,
}

impl Service {
    #[must_use]
    pub fn new(name: &'static str, config: Config, bus: Bus) -> Self {
        let requests = Family::<Handled, Counter>::default();
        let duration =
            Family::<Route, Histogram>::new_with_constructor(|| Histogram::new(DURATION_BUCKETS));
        let mut registry = Registry::default();
        // The client appends `_total` to counter names itself.
        registry.register(
            "intelliops_http_requests",
            "HTTP requests handled",
            requests.clone(),
        );
        registry.register(
            "intelliops_http_request_duration_seconds",
            "HTTP request latency",
            duration.clone(),
        );
        Self {
            name,
            config,
            bus,
            database: None,
            registry,
            requests,
            duration,
        }
    }

    /// Make readiness depend on the database too.
    #[must_use]
    pub fn with_database(mut self, store: Store) -> Self {
        self.database = Some(store);
        self
    }

    fn authorized(&self, request: &Request) -> bool {
        if self.config.get("auth_mode", "off") != "token" {
            return true;
        }
        let expected = self.config.get("auth_token", "");
        let given = request
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.strip_prefix("Bearer "))
            .unwrap_or("")
            .trim();
        !expected.is_empty() && bool::from(given.as_bytes().ct_eq(expected.as_bytes()))
    }
}

/// Wrap a service's own routes in what every service has.
pub fn app(service: Arc<Service>, routes: Router) -> Router {
    let shared = Router::new()
        .route("/health", get(health))
        .route("/ready", get(ready))
        .route("/_metrics", get(metrics))
        .route("/config/posture", get(posture))
        .with_state(service.clone());
    let origins: Vec<HeaderValue> = service
        .config
        .get(
            "cors_origins",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
        .split(',')
        .filter_map(|origin| HeaderValue::from_str(origin.trim()).ok())
        .collect();
    shared
        .merge(routes)
        // The last layer added is outermost: CORS, then metrics (so 401s are
        // counted), then auth.
        .layer(middleware::from_fn_with_state(service.clone(), auth))
        .layer(middleware::from_fn_with_state(service, record))
        .layer(
            CorsLayer::new()
                .allow_origin(AllowOrigin::list(origins))
                .allow_methods(Any)
                .allow_headers(Any),
        )
}

async fn auth(State(service): State<Arc<Service>>, request: Request, next: Next) -> Response {
    let open = request.method() == Method::OPTIONS
        || PROBE_PATHS.contains(&request.uri().path())
        || service.authorized(&request);
    if open {
        next.run(request).await
    } else {
        (
            StatusCode::UNAUTHORIZED,
            Json(json!({"detail": "Unauthorized"})),
        )
            .into_response()
    }
}

async fn record(State(service): State<Arc<Service>>, request: Request, next: Next) -> Response {
    if PROBE_PATHS.contains(&request.uri().path()) {
        return next.run(request).await;
    }
    let method = request.method().to_string();
    // The route template, not the raw URL, to bound label cardinality.
    let path = request
        .extensions()
        .get::<MatchedPath>()
        .map_or("unmatched", MatchedPath::as_str)
        .to_string();
    let started = Instant::now();
    let response = next.run(request).await;
    let name = service.name.to_string();
    service
        .requests
        .get_or_create(&Handled {
            service: name.clone(),
            method: method.clone(),
            path: path.clone(),
            status: response.status().as_u16(),
        })
        .inc();
    service
        .duration
        .get_or_create(&Route {
            service: name,
            method,
            path,
        })
        .observe(started.elapsed().as_secs_f64());
    response
}

async fn health(State(service): State<Arc<Service>>) -> Json<Value> {
    Json(json!({"service": service.name, "status": "ok"}))
}

async fn ready(State(service): State<Arc<Service>>) -> Response {
    let mut failed = Vec::new();
    if let Err(error) = service.bus.ping().await {
        tracing::warn!(%error, "redis is not reachable");
        failed.push("redis");
    }
    if let Some(database) = &service.database
        && let Err(error) = database.ping().await
    {
        tracing::warn!(%error, "postgres is not reachable");
        failed.push("postgres");
    }
    if failed.is_empty() {
        Json(json!({"ready": true})).into_response()
    } else {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"ready": false, "failed": failed})),
        )
            .into_response()
    }
}

async fn posture(State(service): State<Arc<Service>>) -> Json<Value> {
    let mut body = Map::new();
    body.insert("service".into(), service.name.into());
    for (key, default) in POSTURE {
        body.insert(key.into(), service.config.get(key, default).into());
    }
    Json(Value::Object(body))
}

async fn metrics(State(service): State<Arc<Service>>) -> Response {
    let mut body = String::new();
    if encode(&mut body, &service.registry).is_err() {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    }
    (
        [(
            header::CONTENT_TYPE,
            "application/openmetrics-text; version=1.0.0; charset=utf-8",
        )],
        body,
    )
        .into_response()
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use axum::body::Body;
    use axum::routing::post;
    use tower::ServiceExt;

    use super::*;

    fn test_app(pairs: &[(&str, &str)]) -> Router {
        let config = Config::from_vars(
            pairs
                .iter()
                .map(|(k, v)| ((*k).to_string(), (*v).to_string())),
        );
        // Nothing listens here, so readiness must fail.
        let bus = Bus::new("redis://127.0.0.1:1", 0).unwrap();
        let service = Arc::new(Service::new("test-service", config, bus));
        app(service, Router::new().route("/thing", post(|| async {})))
    }

    async fn status(app: Router, method: &str, path: &str, token: Option<&str>) -> StatusCode {
        let mut request = Request::builder().method(method).uri(path);
        if let Some(token) = token {
            request = request.header("authorization", format!("Bearer {token}"));
        }
        let response = app.oneshot(request.body(Body::empty()).unwrap()).await;
        response.unwrap().status()
    }

    #[tokio::test]
    async fn token_mode_gates_everything_but_the_probes() {
        let app = || {
            test_app(&[
                ("INTELLIOPS_AUTH_MODE", "token"),
                ("INTELLIOPS_AUTH_TOKEN", "t0k"),
            ])
        };
        assert_eq!(status(app(), "GET", "/health", None).await, 200);
        assert_eq!(status(app(), "GET", "/_metrics", None).await, 200);
        assert_eq!(status(app(), "POST", "/thing", None).await, 401);
        assert_eq!(status(app(), "POST", "/thing", Some("nope")).await, 401);
        assert_eq!(status(app(), "POST", "/thing", Some("t0k")).await, 200);
    }

    #[tokio::test]
    async fn an_empty_configured_token_authorizes_nobody() {
        let app = test_app(&[("INTELLIOPS_AUTH_MODE", "token")]);
        assert_eq!(status(app, "POST", "/thing", Some("")).await, 401);
    }

    #[tokio::test]
    async fn readiness_reports_a_missing_redis() {
        assert_eq!(status(test_app(&[]), "GET", "/ready", None).await, 503);
    }
}
