use std::process::ExitCode;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use intelliops_bus::{Bus, BusError, Consumer, id_order};
use intelliops_contracts::{DiagnosedSituation, Envelope, RemediationOutcome, Situation, topics};
use intelliops_read::ReadModel;
use intelliops_read::peers::Peers;
use intelliops_read::routes::Read;
use intelliops_service::{Config, Failure, Service, app, bus, consumer_config, port, serve, start};
use serde::de::DeserializeOwned;

const GROUP: &str = "read-model";
const PAGE: usize = 500;
/// Replay order matters: an outcome only applies to a known situation.
const REPLAY: [&str; 4] = [
    topics::SITUATIONS_DETECTED,
    topics::SITUATIONS_DIAGNOSED,
    topics::SITUATIONS_SUPPRESSED,
    topics::REMEDIATION_OUTCOMES,
];

#[tokio::main]
async fn main() -> ExitCode {
    start("read", run).await
}

fn decode<T: DeserializeOwned>(fields: &[(String, String)]) -> Option<T> {
    let fields = fields.iter().map(|(k, v)| (k.as_str(), v.as_str()));
    Envelope::from_fields(fields)
        .and_then(|e| e.decode())
        .map_err(|error| tracing::warn!(%error, "skipping an undecodable entry"))
        .ok()
}

fn apply(model: &mut ReadModel, topic: &str, fields: &[(String, String)]) {
    match topic {
        topics::SITUATIONS_DETECTED => {
            if let Some(s) = decode::<Situation>(fields) {
                model.apply_detected(&s);
            }
        }
        topics::SITUATIONS_DIAGNOSED => {
            if let Some(d) = decode::<DiagnosedSituation>(fields) {
                model.apply_diagnosed(&d);
            }
        }
        topics::SITUATIONS_SUPPRESSED => {
            if decode::<Situation>(fields).is_some() {
                model.apply_suppressed();
            }
        }
        _ => {
            if let Some(o) = decode::<RemediationOutcome>(fields) {
                model.apply_outcome(&o);
            }
        }
    }
}

/// Replay a bounded window into a fresh model, so a restart does not show a
/// blank console. `None` (over the safety limit) means keep the empty model: a
/// partial projection is never served.
async fn rebuild(
    bus: &Bus,
    mut shadow: ReadModel,
    window_seconds: f64,
    max_entries: usize,
) -> Result<Option<ReadModel>, BusError> {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis());
    let window_start = format!(
        "{}-0",
        now_ms.saturating_sub((window_seconds * 1000.0) as u128)
    );
    let mut last_ids = Vec::new();
    for topic in REPLAY {
        // From the earlier of the window and the live group, so nothing falls
        // between the replay and the tail.
        let mut cursor = window_start.clone();
        if let Some(position) = bus.group_position(topic, GROUP).await?
            && id_order(&position) < id_order(&window_start)
        {
            cursor = format!("({position}");
        }
        let mut seen = 0;
        let mut last = None;
        loop {
            let entries = bus.range(topic, &cursor, PAGE).await?;
            let Some((last_id, _)) = entries.last() else {
                break;
            };
            cursor = format!("({last_id}");
            last = Some(last_id.clone());
            for (_, fields) in &entries {
                apply(&mut shadow, topic, fields);
            }
            seen += entries.len();
            if seen > max_entries {
                tracing::warn!(topic, max_entries, "rebuild too large; starting empty");
                return Ok(None);
            }
        }
        last_ids.push((topic, last));
    }
    // Take over the offsets only once every topic replayed cleanly.
    for (topic, last) in last_ids {
        bus.advance_group(topic, GROUP, last.as_deref()).await?;
    }
    Ok(Some(shadow))
}

async fn tail<T: DeserializeOwned>(
    read: Read,
    mut consumer: Consumer,
    apply: impl Fn(&mut ReadModel, T),
) {
    loop {
        let event: T = consumer.next().await;
        apply(&mut read.model(), event);
        read.notify();
    }
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
    let empty = ReadModel::new(
        config.number("read_outcomes_max", 200)?,
        config.number("read_situation_ttl_seconds", 600.0)?,
        config.number("read_situations_max", 50)?,
    );
    let mut model = empty.clone();
    if config.get("read_rebuild_mode", "off") == "replay" {
        let window = config.number("read_rebuild_window_seconds", 3600.0)?;
        let limit = config.number("read_rebuild_max_entries", 20_000)?;
        match rebuild(&bus, empty, window, limit).await {
            Ok(Some(rebuilt)) => model = rebuilt,
            Ok(None) => {}
            Err(error) => tracing::warn!(%error, "rebuild failed; starting empty"),
        }
    }

    let token = (config.get("auth_mode", "off") == "token")
        .then(|| config.get("auth_token", ""))
        .filter(|t| !t.is_empty());
    // In token mode with no token configured, the stream must stay closed.
    let token = match (config.get("auth_mode", "off").as_str(), token) {
        ("token", None) => Some(uuid_like()),
        (_, token) => token,
    };
    let read = Read::new(model, Peers::new(config.clone())?, token);

    // A projection must see every event again on replay, so no dedupe.
    let consumer = |topic: &str| -> Result<Consumer, Failure> {
        let mut settings = consumer_config(&config, topic, GROUP)?;
        settings.dedupe_seconds = None;
        Ok(bus.consumer(settings)?)
    };
    tokio::spawn(tail(
        read.clone(),
        consumer(topics::SITUATIONS_DETECTED)?,
        |m, s: Situation| {
            m.apply_detected(&s);
        },
    ));
    tokio::spawn(tail(
        read.clone(),
        consumer(topics::SITUATIONS_DIAGNOSED)?,
        |m, d: DiagnosedSituation| {
            m.apply_diagnosed(&d);
        },
    ));
    tokio::spawn(tail(
        read.clone(),
        consumer(topics::REMEDIATION_OUTCOMES)?,
        |m, o: RemediationOutcome| m.apply_outcome(&o),
    ));
    tokio::spawn(tail(
        read.clone(),
        consumer(topics::SITUATIONS_SUPPRESSED)?,
        |m, _: Situation| m.apply_suppressed(),
    ));

    let service = Service::new("read-service", config, bus).with_open_get("/stream");
    serve(app(Arc::new(service), read.routes()), port()).await?;
    Ok(())
}

/// An unguessable placeholder that no client can know.
fn uuid_like() -> String {
    use std::hash::{BuildHasher, Hasher, RandomState};
    (0..4)
        .map(|_| format!("{:016x}", RandomState::new().build_hasher().finish()))
        .collect()
}
