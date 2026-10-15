"""Optional SQLAlchemy blob storage; filesystem and S3 adapters do not import it."""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone
from typing import BinaryIO, Iterable, cast

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

from mkb.adapters.object_store import _safe_key
from mkb.ports import Capabilities, Database, ObjectInfo


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

