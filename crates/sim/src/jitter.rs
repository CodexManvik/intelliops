//! A small, bounded, deterministic wander on top of the simulated values, so
//! baselines have spread. Bounded well inside the z-threshold: a healthy
//! service never manufactures an incident.

use std::f64::consts::TAU;

use sha2::{Digest, Sha256};

/// Shorter than the correlator's learning window, or drift reads as anomaly.
const SLOW_PERIOD: f64 = 150.0;
const FAST_PERIOD: f64 = 40.0;
const GRAIN_TICK: f64 = 5.0;

/// Wander as a fraction. Metrics not listed (`service_up`, `db_pool_max`,
/// `tls_handshake_failures`) stay exact.
fn amplitude(metric: &str) -> Option<f64> {
    Some(match metric {
        "cpu_usage" | "request_rate" => 0.13,
        "memory_usage_mb" => 0.055,
        "latency_p50_ms" => 0.17,
        "latency_p99_ms" => 0.21,
        "saturation" => 0.20,
        "queue_depth" => 0.34,
        "db_pool_in_use" => 0.28,
        "disk_usage_percent" => 0.015,
        "meridian_error_rate" => 0.45,
        _ => return None,
    })
}

fn bounds(metric: &str) -> (f64, f64) {
    match metric {
        "cpu_usage" | "disk_usage_percent" => (0.0, 100.0),
        "saturation" | "meridian_error_rate" => (0.0, 1.0),
        _ => (0.0, f64::INFINITY),
    }
}

/// A stable value in `[0, 1)` from a string.
fn unit(seed: &str) -> f64 {
    let digest = Sha256::digest(seed.as_bytes());
    let head = digest
        .iter()
        .take(4)
        .fold(0u32, |acc, b| (acc << 8) | u32::from(*b));
    f64::from(head) / 4_294_967_296.0
}

/// `value` nudged onto its idle curve at `now` (seconds since the epoch).
#[must_use]
pub fn jitter(metric: &str, service: &str, value: f64, now: f64) -> f64 {
    let Some(amplitude) = amplitude(metric) else {
        return value;
    };
    // A real service at rest still drops the odd request.
    let floor = if metric == "meridian_error_rate" {
        0.0035
    } else {
        0.0
    };
    let base = value.max(floor);
    if base == 0.0 {
        return value;
    }
    let wave = |period: f64, name: &str| {
        let phase = unit(&format!("{service}|{metric}|{name}")) * TAU;
        (TAU * now / period + phase).sin()
    };
    let tick = (now / GRAIN_TICK).floor() as i64;
    let grain = unit(&format!("{service}|{metric}|{tick}")) * 2.0 - 1.0;
    let deviation =
        0.50 * wave(SLOW_PERIOD, "slow") + 0.40 * wave(FAST_PERIOD, "fast") + 0.10 * grain;
    // Swing with the headroom, so a faulted gauge does not clip at its ceiling.
    let (low, high) = bounds(metric);
    let room = if high.is_finite() {
        (base - low).min(high - base)
    } else {
        base
    };
    (base + amplitude * room * deviation).max(low).min(high)
}
