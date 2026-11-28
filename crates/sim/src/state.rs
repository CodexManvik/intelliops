//! Fault profiles. Each moves only the metrics that incident would realistically
//! move, or the detector would see extra signals and RCA would misdiagnose.
//! "error" and "dependency outage" in particular leave CPU alone.

use std::time::Instant;

use serde::Deserialize;

pub const CPU_HEALTHY: f64 = 18.0;
const CPU_BROKEN: f64 = 92.0;

fn one() -> f64 {
    1.0
}

#[derive(Debug, Clone, Deserialize)]
pub struct Fault {
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default = "one")]
    pub magnitude: f64,
    #[serde(default)]
    pub duration_seconds: Option<f64>,
}

#[derive(Debug, Clone, Copy)]
struct Ramp {
    from: f64,
    to: f64,
    started: Instant,
    seconds: f64,
}

#[derive(Debug, Clone)]
pub struct State {
    pub cpu: f64,
    pub error_rate: f64,
    /// Injected delay on domain requests.
    pub latency_ms: f64,
    pub request_rate: f64,
    pub latency_p50_ms: f64,
    pub latency_p99_ms: f64,
    pub memory_usage_mb: f64,
    pub saturation: f64,
    pub queue_depth: f64,
    pub db_pool_in_use: f64,
    pub db_pool_max: f64,
    pub disk_usage_percent: f64,
    pub service_up: f64,
    pub tls_handshake_failures: f64,
    ramp: Option<Ramp>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            cpu: CPU_HEALTHY,
            error_rate: 0.0,
            latency_ms: 0.0,
            request_rate: 50.0,
            latency_p50_ms: 20.0,
            latency_p99_ms: 80.0,
            memory_usage_mb: 256.0,
            saturation: 0.1,
            queue_depth: 2.0,
            db_pool_in_use: 3.0,
            db_pool_max: 20.0,
            disk_usage_percent: 35.0,
            service_up: 1.0,
            tls_handshake_failures: 0.4,
            ramp: None,
        }
    }
}

impl State {
    pub fn apply(&mut self, fault: &Fault) {
        let m = fault.magnitude;
        match fault.kind.as_str() {
            "saturation" => {
                self.cpu = (CPU_BROKEN * m).min(100.0);
                self.saturation = (0.95 * m).min(1.0);
                self.queue_depth = 250.0 * m;
            }
            "error" => {
                self.error_rate = m.min(1.0);
                self.cpu = CPU_HEALTHY;
            }
            "latency" => {
                self.latency_ms = 200.0 * m;
                self.latency_p50_ms = (20.0 + 200.0 * m).min(350.0);
                self.latency_p99_ms = (80.0 + 700.0 * m).min(1200.0);
                self.queue_depth = (2.0 + 40.0 * m).min(250.0);
                self.cpu = CPU_BROKEN;
            }
            "crash" => self.service_up = 0.0,
            // A metric family no rule knows, so it escalates to a human.
            "unknown_signal" => {
                self.tls_handshake_failures = (0.4 + 46.0 * m).min(47.0);
            }
            // A leak climbs; everything else steps.
            "memory_leak" => {
                self.ramp = Some(Ramp {
                    from: self.memory_usage_mb,
                    to: self.memory_usage_mb + (768.0 - 256.0) * m,
                    started: Instant::now(),
                    seconds: fault.duration_seconds.unwrap_or(300.0),
                });
            }
            "traffic_surge" => {
                self.request_rate = 400.0 * m;
                self.cpu = (CPU_BROKEN * m).min(100.0);
                self.saturation = (0.95 * m).min(1.0);
                self.queue_depth = 250.0 * m;
            }
            "dependency_outage" => {
                self.error_rate = m.min(1.0);
                self.latency_p99_ms = (80.0 + 700.0 * m).min(1200.0);
                self.cpu = CPU_HEALTHY;
            }
            "db_exhaustion" => {
                self.db_pool_in_use = self.db_pool_max * m;
                self.latency_p99_ms = (80.0 + 500.0 * m).min(900.0);
            }
            _ => {}
        }
    }

    /// Advance a ramp to `elapsed` seconds after it began.
    pub fn sample_at(&mut self, elapsed: f64) {
        if let Some(ramp) = self.ramp {
            let fraction = if ramp.seconds <= 0.0 {
                1.0
            } else {
                (elapsed / ramp.seconds).min(1.0)
            };
            self.memory_usage_mb = ramp.from + (ramp.to - ramp.from) * fraction;
        }
    }

    pub fn sample(&mut self) {
        if let Some(ramp) = self.ramp {
            self.sample_at(ramp.started.elapsed().as_secs_f64());
        }
    }

    pub fn clear(&mut self) {
        *self = Self::default();
    }
}
