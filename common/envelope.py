"""JSON-envelope serialization for putting Pydantic models on the flat bus.

The bus (Redis Streams) carries dict[str, str]. Nested contracts don't fit
flat fields, so a model travels as its JSON string in a single "data" field.
Every service uses these helpers; none hand-rolls serialization (see ADR-001).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def publish_model(bus, topic: str, model: BaseModel) -> None:
    bus.publish(topic, {"data": model.model_dump_json()})


def decode_model(fields: dict, model_type: type[T]) -> T:
    return model_type.model_validate_json(fields["data"])


def iter_models(bus, topic: str, group: str, model_type: type[T]) -> Iterator[T]:
    for fields in bus.consume(topic, group):
        yield decode_model(fields, model_type)
