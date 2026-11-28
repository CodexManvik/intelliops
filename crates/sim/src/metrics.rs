//! The Prometheus text format, with the gauge names RCA matches on.

use std::fmt::Write;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::jitter::jitter;
use crate::state::State;

pub const CONTENT_TYPE: &str = "text/plain; version=0.0.4; charset=utf-8";

pub fn family(out: &mut String, name: &str, kind: &str, help: &str, value: f64) {
    // Writing to a String cannot fail.
    let _ = writeln!(
        out,
        "# HELP {name} {help}\n# TYPE {name} {kind}\n{name} {value}"
    );
}

/// The gauges of `state` except `skip`. `wander` is the service name when
/// jitter is on.
#[must_use]
pub fn render(state: &State, wander: Option<&str>, skip: &[&str]) -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64());
    let mut out = String::new();
    for (name, help, value) in [
        ("cpu_usage", "Simulated CPU utilization percent", state.cpu),
        (
            "meridian_error_rate",
            "Simulated request error rate 0..1",
            state.error_rate,
        ),
        (
            "request_rate",
            "Simulated requests per second",
            state.request_rate,
        ),
        (
            "latency_p50_ms",
            "Simulated p50 latency (ms)",
            state.latency_p50_ms,
        ),
        (
            "latency_p99_ms",
            "Simulated p99 latency (ms)",
            state.latency_p99_ms,
        ),
        (
            "memory_usage_mb",
            "Simulated resident memory (MB)",
            state.memory_usage_mb,
        ),
        (
            "saturation",
            "Simulated USE saturation 0..1",
            state.saturation,
        ),
        ("queue_depth", "Simulated pending work", state.queue_depth),
        (
            "db_pool_in_use",
            "Simulated connections checked out",
            state.db_pool_in_use,
        ),
        (
            "db_pool_max",
            "Simulated connection pool size",
            state.db_pool_max,
        ),
        (
            "disk_usage_percent",
            "Simulated disk utilization percent",
            state.disk_usage_percent,
        ),
        (
            "service_up",
            "1 while serving, 0 when down",
            state.service_up,
        ),
        (
            "tls_handshake_failures",
            "TLS handshake failures per minute - no runbook maps to it",
            state.tls_handshake_failures,
        ),
    ] {
        if skip.contains(&name) {
            continue;
        }
        let value = wander.map_or(value, |service| jitter(name, service, value, now));
        family(&mut out, name, "gauge", help, value);
    }
    out
}
