"""Shared service error/result helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceError(Exception):
    message: str
    code: str = "service_error"
    status_code: int = 400
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.message,
            "code": self.code,
            "status_code": self.status_code,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def error_result(
    message: str,
    *,
    code: str = "service_error",
    status_code: int = 400,
    **details: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": message,
        "code": code,
        "status_code": status_code,
    }
    if details:
        payload["details"] = details
    return payload


def is_error_result(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("error"))


def result_status_code(value: dict[str, Any], default: int = 400) -> int:
    try:
        return int(value.get("status_code") or default)
    except (TypeError, ValueError):
        return default
