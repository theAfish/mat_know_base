"""Infrastructure interfaces used by the public SDK and domain services."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO, Iterable, Protocol


@dataclass(frozen=True)
class ObjectInfo:
    """Backend-neutral object identity and metadata."""

    bucket: str
    key: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None


class Database(Protocol):
    """Minimal relational persistence lifecycle owned by one SDK client."""

    def session(self) -> AbstractContextManager[Any]: ...
    def transaction(self) -> AbstractContextManager[Any]: ...
    def check(self) -> None: ...
    def close(self) -> None: ...


class ObjectStore(Protocol):
    """Object persistence kept separate from relational repositories."""

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None: ...
    def get_bytes(self, bucket: str, key: str) -> bytes: ...
    def open(self, bucket: str, key: str) -> BinaryIO: ...
    def exists(self, bucket: str, key: str) -> bool: ...
    def delete(self, bucket: str, key: str) -> None: ...
    def list(self, bucket: str, prefix: str = "") -> Iterable[ObjectInfo]: ...
    def check(self, buckets: Iterable[str] = ()) -> None: ...
    def close(self) -> None: ...
