"""Cold-start rebuild of the read projection (issue #58).

The projection lives in memory and is fed by the bus, but a consumer group
resumes *past* its prior acks — so a restarted read-service comes up with an
empty console and stays that way until new traffic arrives. The one incident an
operator most wants to see after a restart is exactly the one that is missing.

`rebuild()` replays a bounded window of the raw streams into a SHADOW model and
returns it, leaving the live model untouched until the replay has fully
succeeded. Behind `read_rebuild_mode` (default "off" = today's behaviour).

Two details that are correctness, not style:

- **Topic order matters.** `apply_outcome` mutates a situation only when it is
  already known (`if o.situation_id in self._sits`), so replaying an outcome
  before its detected event silently drops the terminal status, the outcome
  block and the stage timestamp — MTTR, successRate and noiseReduction all come
  back wrong. Detected must land before diagnosed before outcomes.
- **A partial rebuild is never swapped in.** If any topic exceeds
  `read_rebuild_max_entries`, the whole shadow model is discarded and we fall
  back to a normal cold start rather than serve a half-truth.
"""

from __future__ import annotations

import logging
import time

import redis

from common.contracts import DiagnosedSituation, RemediationOutcome, Situation
from common.envelope import decode_model
from services.read.projection import ReadModel

logger = logging.getLogger("intelliops.read.rebuild")

_GROUP = "read-model"
_PAGE = 500

# Same tuples as services/read/consumer.py, but the ORDER here is load-bearing.
_REPLAY_ORDER = [
    ("situations.detected", Situation, "apply_detected"),
    ("situations.diagnosed", DiagnosedSituation, "apply_diagnosed"),
    ("situations.suppressed", Situation, "apply_suppressed"),
    ("remediation.outcomes", RemediationOutcome, "apply_outcome"),
]


def _window_start_id(window_seconds: float) -> str:
    """Redis stream ids are `<ms>-<seq>`, so a timestamp is a valid start id."""
    return f"{int(time.time() * 1000) - int(window_seconds * 1000)}-0"


def _ensure_group(client, topic: str) -> None:
    try:
        client.xgroup_create(topic, _GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:  # already exists
        if "BUSYGROUP" not in str(exc):
            raise


def rebuild(bus, settings) -> ReadModel | None:
    """Replay recent history into a fresh ReadModel, or None to cold-start."""
    if getattr(settings, "read_rebuild_mode", "off") != "replay":
        return None
    client = getattr(bus, "_r", None)
    if client is None:
        # Kafka has no equivalent bounded range read in this change.
        logger.info("read rebuild skipped: bus has no Redis client")
        return None

    shadow = ReadModel(
        max_outcomes=settings.read_outcomes_max,
        ttl_seconds=settings.read_situation_ttl_seconds,
        max_situations=settings.read_situations_max,
    )
    start_id = _window_start_id(settings.read_rebuild_window_seconds)
    max_entries = settings.read_rebuild_max_entries
    last_ids: dict[str, str] = {}
    total = 0

    for topic, model_type, method in _REPLAY_ORDER:
        apply = getattr(shadow, method)
        cursor = start_id
        seen = 0
        while True:
            try:
                entries = client.xrange(topic, min=cursor, max="+", count=_PAGE)
            except redis.ResponseError as exc:
                logger.warning("read rebuild: cannot range %s (%s); aborting", topic, exc)
                return None
            if not entries:
                break
            for entry_id, fields in entries:
                try:
                    apply(decode_model(fields, model_type))
                except Exception as exc:  # noqa: BLE001 - one bad row must not abort the replay
                    logger.warning(
                        "read rebuild: undecodable entry %s on %s: %s", entry_id, topic, exc
                    )
                last_ids[topic] = entry_id
                seen += 1
            if seen > max_entries:
                logger.warning(
                    "read rebuild: %s exceeded read_rebuild_max_entries (%s); "
                    "discarding the whole rebuild and cold-starting instead",
                    topic,
                    max_entries,
                )
                return None
            # xrange's min is inclusive, so step past the last id we just read.
            cursor = f"({entries[-1][0]}"
        total += seen

    # Only now that every topic replayed cleanly do we take ownership of the
    # offsets: point the live consumer group past what the shadow already has.
    for topic, last_id in last_ids.items():
        try:
            _ensure_group(client, topic)
            client.xgroup_setid(topic, _GROUP, id=last_id)
        except redis.ResponseError as exc:
            logger.warning("read rebuild: could not set group id on %s: %s", topic, exc)

    logger.info(
        "read rebuild: replayed %s entries across %s topics (window %.0fs)",
        total,
        len(last_ids),
        settings.read_rebuild_window_seconds,
    )
    return shadow
