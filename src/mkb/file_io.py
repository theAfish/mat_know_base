"""Bounded binary I/O shared by object adapters and file-based consumers."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import BinaryIO

CHUNK_SIZE = 256 * 1024


def verify_file(size: int, sha256: str, *, expected_size=None, expected_sha256=None):
    if expected_size is not None and size != expected_size:
        raise ValueError("File size differs from its expected metadata")
    if expected_sha256 is not None and sha256 != expected_sha256:
        raise ValueError("File checksum differs from its expected metadata")


def stream_chunks(stream: BinaryIO, *, max_bytes: int | None = None,
                  chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    if type(chunk_size) is not int or not 0 < chunk_size <= 8 * 1024 * 1024:
        raise ValueError("chunk_size must be between 1 byte and 8 MiB")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        raise ValueError("max_bytes must be a nonnegative integer")
    size = 0
    while True:
        amount = chunk_size if max_bytes is None else min(chunk_size, max_bytes - size + 1)
        data = stream.read(amount)
        if not isinstance(data, bytes) or len(data) > amount:
            raise ValueError("A binary stream must return bounded bytes")
        if not data:
            return
        size += len(data)
        if max_bytes is not None and size > max_bytes:
            raise ValueError("File exceeds its transfer byte limit")
        yield data


@contextmanager
def seekable_file(source):
    """Borrow caller-owned handles; spool nonseekable inputs to disk, never RAM."""
    if isinstance(source, (str, Path)):
        with Path(source).open("rb") as stream:
            yield stream
    elif getattr(source, "seekable", lambda: False)():
        yield source
    else:
        with tempfile.TemporaryFile("w+b") as stream:
            for chunk in stream_chunks(source):
                stream.write(chunk)
            stream.seek(0)
            yield stream
