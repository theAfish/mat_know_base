"""Explicit application object for the evolving public Python SDK.

This first boundary deliberately delegates to the existing, data-compatible service
facade.  Later adapters can replace the bindings one subsystem at a time without
changing callers or migrating the existing database prematurely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

from mkb.exceptions import MKBError, ValidationError
from mkb.pipelines import Pipelines
from mkb.ports import Database, ObjectStore
from mkb.repositories import (
    Artifacts,
    Collections,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
)


class ServiceBindings(Protocol):
    """Callable service namespace consumed by :class:`KnowledgeBase`.

    The compatibility implementation is ``mkb.api``. Tests and future adapters can
    provide isolated namespaces without mutating module globals.
    """

    def setup(self) -> None: ...
    def ingest(self, directory, label=None, *, user_named=False) -> dict: ...
    def sync(self, root_dir) -> dict: ...
    def sync_project(self, project_id) -> dict: ...
    def process(self, project_id=None, progress_callback=None) -> dict: ...
    def extract(self, project_id=None, **kwargs) -> dict: ...
    def list_projects(self, limit=50) -> list[dict]: ...
    def list_assets(self, project_id=None, limit=100) -> list[dict]: ...
    def list_processed_assets(self, project_id=None, limit=100) -> list[dict]: ...
    def list_frames(self, status=None) -> list[dict]: ...
    def get_frame(self, project_id) -> dict | None: ...
    def create_space(self, **kwargs) -> dict: ...
    def get_space(self, space_id_or_name) -> dict | None: ...
    def list_spaces(self) -> list[dict]: ...
    def project(self, space_id, **kwargs) -> dict: ...
    def get_projection(self, projection_id) -> dict | None: ...
    def list_projections(self, **kwargs) -> list[dict]: ...


class _UnavailableServiceBindings:
    """Prevents explicit clients from accidentally falling back to global services."""

    def close(self) -> None:
        return None

    def __getattr__(self, name: str):
        raise MKBError(
            f"Compatibility operation {name!r} is unavailable on an explicitly "
            "configured client; use a grouped SDK service"
        )


@dataclass(frozen=True)
class MKBConfig:
    """Non-secret topology for one SDK instance.

    Secret fields are excluded from ``repr`` and passed only to backend adapters.
    This object is intentionally immutable so one client's configuration cannot leak
    into another client.
    """

    database_url: str | None = field(default=None, repr=False)
    object_store_endpoint: str | None = None
    object_store_access_key: str | None = field(default=None, repr=False)
    object_store_secret_key: str | None = field(default=None, repr=False)
    raw_bucket: str = "raw"
    processed_bucket: str = "processed"
    archive_bucket: str = "archive"
    temp_bucket: str = "temp"

    @classmethod
    def from_environment(cls) -> "MKBConfig":
        """Capture the current compatibility configuration explicitly."""
        from mkb.config import settings

        return cls(
            database_url=settings.pg_dsn_sync,
            object_store_endpoint=settings.s3_endpoint,
            object_store_access_key=settings.s3_access_key,
            object_store_secret_key=settings.s3_secret_key,
            raw_bucket=settings.s3_bucket_raw,
            processed_bucket=settings.s3_bucket_processed,
            archive_bucket=settings.s3_bucket_archive,
            temp_bucket=settings.s3_bucket_temp,
        )


class KnowledgeBase:
    """Configured, non-global entry point for Python callers.

    ``from_environment`` preserves all current PostgreSQL/MinIO data by binding the
    client to the existing supported service facade. Direct construction is intended
    for tests and future independently configured adapters.
    """

    def __init__(
        self,
        *,
        services: ServiceBindings | None = None,
        config: MKBConfig | None = None,
        database: Database | None = None,
        object_store: ObjectStore | None = None,
        collections: Collections | None = None,
        sources: Sources | None = None,
        artifacts: Artifacts | None = None,
        records: Records | None = None,
        schemas: ExtractionSchemas | None = None,
        projections: Projections | None = None,
        capabilities: frozenset[str] | None = None,
    ):
        self._services = services if services is not None else _UnavailableServiceBindings()
        self.config = config or MKBConfig()
        self.database = database
        self.object_store = object_store
        self.collections = collections
        self.sources = sources
        self.artifacts = artifacts
        self.records = records
        self.schemas = schemas
        self.projections = projections
        detected_capabilities = set(capabilities or ())
        if database is not None:
            detected_capabilities.add("transactions")
        if object_store is not None:
            detected_capabilities.add("object_streaming")
        self.pipelines = Pipelines(self, capabilities=frozenset(detected_capabilities))
        self._closed = False

    @classmethod
    def from_environment(cls) -> "KnowledgeBase":
        from mkb import api
        from mkb.adapters import (
            S3ObjectStore,
            SQLAlchemyDatabase,
        )

        config = MKBConfig.from_environment()
        database = SQLAlchemyDatabase(config.database_url)
        object_store = S3ObjectStore(
            endpoint_url=config.object_store_endpoint,
            access_key=config.object_store_access_key,
            secret_key=config.object_store_secret_key,
        )
        return cls._from_resources(
            services=api,
            config=config,
            database=database,
            object_store=object_store,
        )

    @classmethod
    def from_url(
        cls,
        *,
        database_url: str,
        object_store_url: str | None = None,
        object_store_access_key: str | None = None,
        object_store_secret_key: str | None = None,
        capabilities: frozenset[str] | None = None,
    ) -> "KnowledgeBase":
        """Create an independent client without reading global environment settings.

        Supported object-store URLs are ``file:///absolute/root`` and
        ``s3://bucket?endpoint=http://host:9000``. This constructor never runs schema
        migrations and does not enable legacy global service calls.
        """
        from mkb.adapters import FileObjectStore, S3ObjectStore, SQLAlchemyDatabase

        if not database_url.strip():
            raise ValidationError("database_url must not be empty")
        database = SQLAlchemyDatabase(database_url)
        object_store = None
        endpoint = None
        raw_bucket = "raw"
        try:
            if object_store_url is not None:
                parsed = urlparse(object_store_url)
                if parsed.scheme == "file":
                    if not parsed.path or not parsed.path.startswith("/"):
                        raise ValidationError("file object-store URL must use an absolute path")
                    object_store = FileObjectStore(unquote(parsed.path))
                elif parsed.scheme == "s3":
                    if not parsed.netloc:
                        raise ValidationError("s3 object-store URL must include a bucket")
                    raw_bucket = parsed.netloc
                    endpoint = parse_qs(parsed.query).get("endpoint", [None])[0]
                    object_store = S3ObjectStore(
                        endpoint_url=endpoint,
                        access_key=object_store_access_key,
                        secret_key=object_store_secret_key,
                    )
                else:
                    raise ValidationError(
                        "object_store_url must use the file or s3 scheme"
                    )

            config = MKBConfig(
                database_url=database_url,
                object_store_endpoint=endpoint,
                object_store_access_key=object_store_access_key,
                object_store_secret_key=object_store_secret_key,
                raw_bucket=raw_bucket,
            )
            return cls._from_resources(
                config=config,
                database=database,
                object_store=object_store,
                capabilities=capabilities,
            )
        except Exception:
            if object_store is not None:
                object_store.close()
            database.close()
            raise

    @classmethod
    def _from_resources(
        cls,
        *,
        config: MKBConfig,
        database: Database,
        object_store: ObjectStore | None,
        services: ServiceBindings | None = None,
        capabilities: frozenset[str] | None = None,
    ) -> "KnowledgeBase":
        from mkb.adapters import (
            SQLAlchemyArtifactRepository,
            SQLAlchemyCollectionRepository,
            SQLAlchemyExtractionSchemaRepository,
            SQLAlchemyProjectionRepository,
            SQLAlchemyRecordRepository,
            SQLAlchemySourceRepository,
        )

        return cls(
            services=services,
            config=config,
            database=database,
            object_store=object_store,
            collections=Collections(SQLAlchemyCollectionRepository(database)),
            sources=(
                Sources(SQLAlchemySourceRepository(database), object_store)
                if object_store is not None
                else None
            ),
            artifacts=(
                Artifacts(SQLAlchemyArtifactRepository(database), object_store)
                if object_store is not None
                else None
            ),
            records=Records(SQLAlchemyRecordRepository(database)),
            schemas=ExtractionSchemas(SQLAlchemyExtractionSchemaRepository(database)),
            projections=Projections(SQLAlchemyProjectionRepository(database)),
            capabilities=capabilities,
        )

    def __enter__(self) -> "KnowledgeBase":
        self._ensure_open()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        close = getattr(self._services, "close", None)
        if callable(close):
            close()
        if self.object_store is not None:
            self.object_store.close()
        if self.database is not None:
            self.database.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("KnowledgeBase is closed")

    def _call(self, name: str, *args, **kwargs):
        self._ensure_open()
        operation = getattr(self._services, name)
        return operation(*args, **kwargs)

    # Current end-to-end lifecycle. These explicit methods are intentionally small;
    # domain-specific grouped APIs will be added without relying on __getattr__.
    def setup(self) -> None:
        return self._call("setup")

    def ingest(self, directory, label=None, *, user_named=False) -> dict:
        return self._call("ingest", directory, label=label, user_named=user_named)

    def sync(self, root_dir) -> dict:
        return self._call("sync", root_dir)

    def sync_project(self, project_id) -> dict:
        return self._call("sync_project", project_id)

    def process(self, project_id=None, progress_callback=None) -> dict:
        return self._call(
            "process", project_id=project_id, progress_callback=progress_callback
        )

    def extract(self, project_id=None, **kwargs) -> dict:
        return self._call("extract", project_id=project_id, **kwargs)

    def list_projects(self, limit=50) -> list[dict]:
        return self._call("list_projects", limit=limit)

    def list_assets(self, project_id=None, limit=100) -> list[dict]:
        return self._call("list_assets", project_id=project_id, limit=limit)

    def list_processed_assets(self, project_id=None, limit=100) -> list[dict]:
        return self._call("list_processed_assets", project_id=project_id, limit=limit)

    def list_frames(self, status=None) -> list[dict]:
        return self._call("list_frames", status=status)

    def get_frame(self, project_id) -> dict | None:
        return self._call("get_frame", project_id)

    def create_space(self, **kwargs) -> dict:
        return self._call("create_space", **kwargs)

    def get_space(self, space_id_or_name) -> dict | None:
        return self._call("get_space", space_id_or_name)

    def list_spaces(self) -> list[dict]:
        return self._call("list_spaces")

    def project(self, space_id, **kwargs) -> dict:
        return self._call("project", space_id, **kwargs)

    def get_projection(self, projection_id) -> dict | None:
        return self._call("get_projection", projection_id)

    def list_projections(self, **kwargs) -> list[dict]:
        return self._call("list_projections", **kwargs)

    def service(self, name: str) -> Any:
        """Temporary escape hatch for compatibility operations not migrated yet."""
        self._ensure_open()
        return getattr(self._services, name)
