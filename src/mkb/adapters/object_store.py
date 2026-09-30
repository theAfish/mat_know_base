"""Instance-owned SQL, S3 and filesystem object-store adapters."""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Iterable, cast

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    LargeBinary,
    MetaData,
    String,
    Table,
    delete,
    insert,
    select,
)

from mkb.ports import Capabilities, ObjectInfo

if TYPE_CHECKING:
    from mkb.ports import Database


def _safe_key(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if not key or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe object key: {key!r}")
    return path


class S3ObjectStore:
    """S3-compatible object store with no dependency on global settings."""

    capabilities = frozenset({Capabilities.OBJECT_STREAMING})

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region_name: str = "us-east-1",
        client=None,
    ):
        if client is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
            except ImportError as exc:
                raise RuntimeError(
                    "The S3 adapter requires the optional boto3 dependency"
                ) from exc
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=BotoConfig(signature_version="s3v4"),
                region_name=region_name,
            )
        self._client = client
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Object-store adapter is closed")

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        self._ensure_open()
        _safe_key(key)
        self._client.put_object(Bucket=bucket, Key=key, Body=data)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        body = self.open(bucket, key)
        try:
            return body.read()
        finally:
            body.close()

    def open(self, bucket: str, key: str) -> BinaryIO:
        self._ensure_open()
        _safe_key(key)
        body = self._client.get_object(Bucket=bucket, Key=key)["Body"]
        return cast(BinaryIO, body)

    def exists(self, bucket: str, key: str) -> bool:
        self._ensure_open()
        _safe_key(key)
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except Exception as exc:
            # Keep botocore optional for filesystem-only installations and for
            # callers that inject another S3-compatible client implementation.
            if not hasattr(exc, "response"):
                raise
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

    capabilities = frozenset({Capabilities.OBJECT_STREAMING})

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


# Kept in its own MetaData so creating the blob table never drags repository
# DDL along with it.
object_store_metadata = MetaData()

# ``key`` is bounded so the composite primary-key index entry stays inside the
# PostgreSQL btree tuple limit. Real keys are content-addressed
# (``ab/cd/<64 hex>``) or short relative paths, so this is far above practice.
object_blobs_table = Table(
    "object_blobs",
    object_store_metadata,
    Column("bucket", String(63), primary_key=True),
    Column("key", String(512), primary_key=True),
    Column("data", LargeBinary, nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("etag", String(64), nullable=False),
    Column("last_modified", DateTime(timezone=True), nullable=False),
)


class SqlObjectStore:
    """Object store that keeps bytes in the relational database.

    This removes the separate object-storage service from a deployment: the
    database is then the only stateful dependency and the only backup artifact.
    Only portable SQLAlchemy types are used, so the same adapter runs on
    PostgreSQL and on SQLite.

    The adapter borrows the caller's :class:`~mkb.ports.Database` and never owns
    its engine, so :meth:`close` must not dispose it.
    """

    capabilities = frozenset({Capabilities.OBJECT_STREAMING})

    def __init__(self, database: Database, *, auto_create: bool = True):
        self._database = database
        self._closed = False
        if auto_create:
            self._create_table()

    def _create_table(self) -> None:
        # Schema migrations were retired, so the adapter provisions its own
        # table the same additive way the generic repositories do.
        with self._database.transaction() as session:
            object_store_metadata.create_all(session.connection(), checkfirst=True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Object-store adapter is closed")

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        self._ensure_open()
        _safe_key(bucket)
        _safe_key(key)
        # Content-addressed writes re-put existing keys, so overwrite. Delete
        # then insert keeps this portable across dialects; both statements run
        # inside one transaction.
        with self._database.transaction() as session:
            session.execute(
                delete(object_blobs_table).where(
                    object_blobs_table.c.bucket == bucket,
                    object_blobs_table.c.key == key,
                )
            )
            session.execute(
                insert(object_blobs_table).values(
                    bucket=bucket,
                    key=key,
                    data=data,
                    size=len(data),
                    etag=hashlib.sha256(data).hexdigest(),
                    last_modified=datetime.now(timezone.utc),
                )
            )

    def get_bytes(self, bucket: str, key: str) -> bytes:
        self._ensure_open()
        _safe_key(bucket)
        _safe_key(key)
        with self._database.transaction() as session:
            data = session.execute(
                select(object_blobs_table.c.data).where(
                    object_blobs_table.c.bucket == bucket,
                    object_blobs_table.c.key == key,
                )
            ).scalar_one_or_none()
        if data is None:
            raise FileNotFoundError(f"Object does not exist: {bucket}/{key}")
        return bytes(data)

    def open(self, bucket: str, key: str) -> BinaryIO:
        # Rows are read whole, so this buffers rather than streams. Acceptable
        # because every caller already consumes the full object and uploads are
        # size-capped.
        return cast(BinaryIO, io.BytesIO(self.get_bytes(bucket, key)))

    def exists(self, bucket: str, key: str) -> bool:
        self._ensure_open()
        _safe_key(bucket)
        _safe_key(key)
        with self._database.transaction() as session:
            found = session.execute(
                select(object_blobs_table.c.key).where(
                    object_blobs_table.c.bucket == bucket,
                    object_blobs_table.c.key == key,
                )
            ).scalar_one_or_none()
        return found is not None

    def delete(self, bucket: str, key: str) -> None:
        self._ensure_open()
        _safe_key(bucket)
        _safe_key(key)
        with self._database.transaction() as session:
            session.execute(
                delete(object_blobs_table).where(
                    object_blobs_table.c.bucket == bucket,
                    object_blobs_table.c.key == key,
                )
            )

    def list(self, bucket: str, prefix: str = "") -> Iterable[ObjectInfo]:
        self._ensure_open()
        _safe_key(bucket)
        if prefix:
            _safe_key(prefix)
        # The payload column is deliberately excluded; listing must stay cheap.
        statement = select(
            object_blobs_table.c.key,
            object_blobs_table.c.size,
            object_blobs_table.c.etag,
            object_blobs_table.c.last_modified,
        ).where(object_blobs_table.c.bucket == bucket)
        if prefix:
            statement = statement.where(object_blobs_table.c.key.startswith(prefix))
        with self._database.transaction() as session:
            rows = session.execute(statement.order_by(object_blobs_table.c.key)).all()
        return [
            ObjectInfo(
                bucket=bucket,
                key=row.key,
                size=int(row.size),
                etag=row.etag,
                last_modified=row.last_modified,
            )
            for row in rows
        ]

    def check(self, buckets: Iterable[str] = ()) -> None:
        self._ensure_open()
        # Buckets are implicit namespaces rather than provisioned containers,
        # so an unused name is not an error and no bootstrap step is required.
        for bucket in buckets:
            _safe_key(bucket)
        with self._database.transaction() as session:
            session.execute(select(object_blobs_table.c.bucket).limit(1))

    def close(self) -> None:
        # The database is owned by the caller; closing it here would break every
        # repository sharing the engine.
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


def create_object_store(
    backend: str,
    *,
    database: Database | None = None,
    root: str | Path | None = None,
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
):
    """Build the object store named by ``backend``.

    Takes every input explicitly so both the SDK and the service layer can
    select a backend without either of them reading global settings.
    """
    if backend == "sql":
        if database is None:
            raise ValueError("The sql object-store backend requires a database")
        return SqlObjectStore(database)
    if backend == "file":
        if root is None:
            raise ValueError("The file object-store backend requires a root path")
        return FileObjectStore(root)
    if backend == "s3":
        return S3ObjectStore(
            endpoint_url=endpoint,
            access_key=access_key,
            secret_key=secret_key,
        )
    raise ValueError(
        f"Unknown object-store backend: {backend!r} (expected sql, file or s3)"
    )
