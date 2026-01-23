from __future__ import annotations

import json
import os
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class JsonlEventLogger:
    def __init__(self, path: str) -> None:
        self._path = path

    def handle(self, event: object) -> None:
        payload = {
            "event_type": type(event).__name__,
            "event": _serialize(event),
        }
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))
            handle.write("\n")


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(val) for key, val in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _format_datetime(value: datetime) -> str:
    try:
        return value.isoformat(timespec="microseconds")
    except TypeError:
        return value.isoformat()
