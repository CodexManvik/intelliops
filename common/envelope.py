"""JSON-envelope serialization for putting Pydantic models on the flat bus.

Each published message also carries an `id` -- a stable event identity used for
idempotent redelivery under at-least-once (issue #53). It is additive: decoding
ignores it, so messages published before it existed still decode.

The bus (Redis Streams) carries dict[str, str]. Nested contracts don't fit
flat fields, so a model travels as its JSON string in a single "data" field.
Every service uses these helpers; none hand-rolls serialization (see ADR-001).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel

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


def iter_models(bus, topic: str, group: str, model_type: type[T]) -> Iterator[T]:
    for fields in bus.consume(topic, group):
        yield decode_model(fields, model_type)
