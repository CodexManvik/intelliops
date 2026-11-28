//! Build the engine from `INTELLIOPS_CORRELATION_*` and friends.

use intelliops_detect::{Detector, Policy, Robust, RobustConfig, Thresholds};
use intelliops_service::Config;

use crate::{Engine, EngineConfig, Grouping, Scorer, SuppressionMode};

fn policy(config: &Config) -> Result<Policy, String> {
    if config.get("detection_policy", "off") != "on" {
        return Ok(Policy::Off);
    }
    let defaults = Thresholds::default();
    Ok(Policy::On(Thresholds {
        ratio: config.number("detection_ratio_threshold", defaults.ratio)?,
        saturation_ratio: config.number(
            "detection_saturation_ratio_threshold",
            defaults.saturation_ratio,
        )?,
        saturation_percent: config.number(
            "detection_saturation_percent_threshold",
            defaults.saturation_percent,
        )?,
        latency_ceiling_ms: config
            .number("detection_latency_ceiling_ms", defaults.latency_ceiling_ms)?,
    }))
}

fn robust(config: &Config, per_series: bool) -> Result<RobustConfig, String> {
    Ok(RobustConfig {
        warmup_samples: config.number("correlation_robust_warmup", 30)?,
        seasonal_buckets: config.number("correlation_seasonal_buckets", 24)?,
        window_size: config.number("correlation_robust_window", 128)?,
        per_series,
    })
}

fn scorer(config: &Config) -> Result<Scorer, String> {
    match config.get("correlator_kind", "river").as_str() {
        "river" => Ok(Scorer::River(Detector::new(
            config.number("correlation_warmup_samples", 50)?,
        ))),
        "robust" => {
            let per_series = config.get("correlation_robust_key_by", "metric") == "series";
            Ok(Scorer::Robust(Robust::new(robust(config, per_series)?)))
        }
        "trained" => Ok(Scorer::Trained(
            Robust::new(robust(config, false)?),
            Box::default(),
        )),
        other => Err(format!("unknown CORRELATOR_KIND: {other:?}")),
    }
}

pub fn engine_from(config: &Config) -> Result<Engine, String> {
    let defaults = EngineConfig::default();
    let settings = EngineConfig {
        z_threshold: config.number("correlation_z_threshold", defaults.z_threshold)?,
        policy: policy(config)?,
        window_seconds: config.number("correlation_window_seconds", defaults.window_seconds)?,
        grouping: if config.get("correlation_group_by", "window") == "service" {
            Grouping::Service
        } else {
            Grouping::Window
        },
        min_events: config.number("correlation_min_events", 1)?,
        suppress_threshold: config.number("reliability_suppress_threshold", 0.8)?,
        suppress_min_samples: config.number("reliability_suppress_min_samples", 3)?,
        suppress_mode: if config.get("suppression_mode", "quiet") == "quiet" {
            SuppressionMode::Quiet
        } else {
            SuppressionMode::Drop
        },
    };
    Ok(Engine::new(settings, scorer(config)?))
}
