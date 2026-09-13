"""JSON-envelope serialization for putting Pydantic models on the flat bus.

Each published message also carries an `id` -- a stable event identity used for
idempotent redelivery under at-least-once (issue #53). It is additive: decoding
ignores it, so messages published before it existed still decode.

The bus (Redis Streams) carries dict[str, str]. Nested contracts don't fit
flat fields, so a model travels as its JSON string in a single "data" field.
Every service uses these helpers; none hand-rolls serialization (see ADR-001).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def publish_model(bus, topic: str, model: BaseModel) -> None:
    # `id` is a stable, backend-neutral event identity: it is what a consumer
    # dedupes on when at-least-once redelivers, and it survives a DLQ replay
    # re-emitting the same envelope. A transport id (the Redis entry id) would
    # do neither. decode_model reads only "data", so this is inert for every
    # existing consumer and for backlog entries published before it existed.
    bus.publish(topic, {"data": model.model_dump_json(), "id": uuid4().hex})


def decode_model(fields: dict, model_type: type[T]) -> T:
    return model_type.model_validate_json(fields["data"])


def iter_models(
    bus, topic: str, group: str, model_type: type[T], *, guard=None, dlq=None
) -> Iterator[T]:
    """Decode each message on `topic` into `model_type`.

    `guard` (an IdempotencyGuard) and `dlq` (anything with `send_to_dlq`, in
    practice the bus) are optional and default to None, so existing call sites
    are byte-identical.

    The DLQ belongs HERE, not in the bus: a generator cannot see the exception
    its consumer raised - when decode fails the bus generator receives
    GeneratorExit, not the ValidationError. This frame is the one that actually
    calls decode_model, so it is the only place a poison payload is visible.

    Skipping (`continue`) resumes `bus.consume`, which is what acks the entry
    under at-least-once - so a parked or already-seen message does not come
    back forever.
    """
    for fields in bus.consume(topic, group):
        try:
            parsed = decode_model(fields, model_type)
        except ValidationError as exc:
            if dlq is None:
                raise  # today's behaviour, exactly: the consumer thread dies
            dlq.send_to_dlq(topic, fields, f"decode:{exc.__class__.__name__}", group)
            logger.warning(
                "undecodable message on %s parked in the DLQ (%s)", topic, exc.__class__.__name__
            )
            continue
        if guard is not None:
            event_id = fields.get("id")
            # No id means the message predates the envelope id; let it through
            # rather than silently dropping a legitimate backlog entry.
            if event_id is not None and not guard.claim(f"{group}:{event_id}"):
                logger.debug("skipping already-processed event %s on %s", event_id, topic)
                continue
        yield parsed
