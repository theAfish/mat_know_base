"""Instance-owned S3 and filesystem object-store adapters."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from mkb.ports import ObjectInfo


def _safe_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if not key or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe object key: {key!r}")
    return path


class S3ObjectStore:
    """S3-compatible object store with no dependency on global settings."""

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region_name: str = "us-east-1",
        client=None,
    ):
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name=region_name,
        )
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Object-store adapter is closed")

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        self._ensure_open()
        _safe_key(key)
        self._client.put_object(Bucket=bucket, Key=key, Body=data)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        self._ensure_open()
        _safe_key(key)
        return self._client.get_object(Bucket=bucket, Key=key)["Body"].read()

    def open(self, bucket: str, key: str) -> BinaryIO:
        return io.BytesIO(self.get_bytes(bucket, key))

    def exists(self, bucket: str, key: str) -> bool:
        self._ensure_open()
        _safe_key(key)
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            code = str((exc.response or {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete(self, bucket: str, key: str) -> None:
        self._ensure_open()
        _safe_key(key)
        self._client.delete_object(Bucket=bucket, Key=key)

    def list(self, bucket: str, prefix: str = "") -> Iterable[ObjectInfo]:
        self._ensure_open()
        if prefix:
            _safe_key(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield ObjectInfo(
                    bucket=bucket,
                    key=item["Key"],
                    size=int(item.get("Size") or 0),
                    etag=str(item.get("ETag") or "").strip('"') or None,
                    last_modified=item.get("LastModified"),
                )

    def check(self, buckets: Iterable[str] = ()) -> None:
        self._ensure_open()
        for bucket in buckets:
            self._client.head_bucket(Bucket=bucket)

    def close(self) -> None:
        if self._closed:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class FileObjectStore:
    """Filesystem object store suitable for local SDK use and tests."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Object-store adapter is closed")

    def _path(self, bucket: str, key: str) -> Path:
        self._ensure_open()
        bucket_path = _safe_key(bucket)
        key_path = _safe_key(key)
        return self.root.joinpath(*bucket_path.parts, *key_path.parts)

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self._path(bucket, key).read_bytes()

    def open(self, bucket: str, key: str) -> BinaryIO:
        return self._path(bucket, key).open("rb")

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).is_file()

    def delete(self, bucket: str, key: str) -> None:
        self._path(bucket, key).unlink(missing_ok=True)

    def list(self, bucket: str, prefix: str = "") -> Iterable[ObjectInfo]:
        base = self.root.joinpath(*_safe_key(bucket).parts)
        if not base.exists():
            return
        prefix_path = PurePosixPath(prefix) if prefix else None
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            key = path.relative_to(base).as_posix()
            if prefix_path is not None and not key.startswith(prefix_path.as_posix()):
                continue
            stat = path.stat()
            yield ObjectInfo(
                bucket=bucket,
                key=key,
                size=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime).astimezone(),
            )

    def check(self, buckets: Iterable[str] = ()) -> None:
        self._ensure_open()
        for bucket in buckets:
            path = self.root.joinpath(*_safe_key(bucket).parts)
            if not path.is_dir():
                raise FileNotFoundError(f"Object-store bucket does not exist: {bucket}")

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed
