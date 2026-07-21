"""Explicit application object for the evolving public Python SDK.

This first boundary deliberately delegates to the existing, data-compatible service
facade.  Later adapters can replace the bindings one subsystem at a time without
changing callers or migrating the existing database prematurely.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

from mkb.exceptions import ConflictError, MKBError, ValidationError
from mkb.application_services import (
    AssistantService,
    MaintenanceService,
    SettingsService,
)
from mkb.graph import Graph
from mkb.job_service import Jobs
from mkb.managed_services import Feedback, PostProcessors, Skills
from mkb.materials import (
    MaterialFeedback,
    MaterialFrames,
    MaterialGraph,
    MaterialLibrary,
    MaterialProjections,
    Materials,
    MaterialSpaces,
    MaterialWorkflows,
)
from mkb.pipelines import Pipelines
from mkb.ports import (
    Capabilities,
    Database,
    GraphStore,
    JobBackend,
    ModelProvider,
    ObjectStore,
    VectorSearch,
)
from mkb.repositories import (
    Artifacts,
    CollectionGroups,
    Collections,
    EvidenceLinks,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
    Workflows,
)
from mkb.registries import Parsers, Steps
from mkb.transactions import Transaction


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
    allow_uploaded_python: bool = False
    upload_max_file_mb: int = 100
    api_host: str = "127.0.0.1"
    api_port: int = 8000

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
            allow_uploaded_python=settings.allow_uploaded_python,
            upload_max_file_mb=settings.upload_max_file_mb,
            api_host=settings.api_host,
            api_port=settings.api_port,
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
        graph_store: GraphStore | None = None,
        model_provider: ModelProvider | None = None,
        job_backend: JobBackend | None = None,
        vector_search: VectorSearch | None = None,
        collections: Collections | None = None,
        sources: Sources | None = None,
        artifacts: Artifacts | None = None,
        records: Records | None = None,
        schemas: ExtractionSchemas | None = None,
        projections: Projections | None = None,
        evidence: EvidenceLinks | None = None,
        materials: Materials | None = None,
        capabilities: frozenset[str] | None = None,
        schema_manager: Any | None = None,
        transaction_factory: Callable[[Any], Transaction] | None = None,
        parser_registry_factory: Callable[["KnowledgeBase"], Parsers] | None = None,
        pipeline_registry_factory: Callable[
            ["KnowledgeBase", frozenset[str]], Pipelines
        ]
        | None = None,
        steps: Steps | None = None,
        feedback: Feedback | None = None,
        skills: Skills | None = None,
        post_processors: PostProcessors | None = None,
        runtime_settings_reader: Callable[[], dict[str, Any]] | None = None,
        runtime_settings_updater: (
            Callable[[dict[str, Any]], dict[str, Any]] | None
        ) = None,
        startup_validator: Callable[..., list[str]] | None = None,
        migration_inventory_reader: Callable[[], dict[str, Any]] | None = None,
        cleanup_executor: Callable[..., dict[str, Any]] | None = None,
    ):
        self._services = services if services is not None else _UnavailableServiceBindings()
        self.config = config or MKBConfig()
        self.database = database
        self.object_store = object_store
        self.graph_store = graph_store
        self.graph = (
            Graph(graph_store, model_provider=model_provider)
            if graph_store is not None
            else None
        )
        self.model_provider = model_provider
        self.job_backend = job_backend
        self.jobs = Jobs(job_backend)
        self.vector_search = vector_search
        self.collections = collections
        self.sources = sources
        self.artifacts = artifacts
        self.records = records
        self.schemas = schemas
        self.projections = projections
        self.evidence = evidence
        self.materials = materials if materials is not None else Materials()
        self.feedback = feedback if feedback is not None else Feedback(None)
        self.skills = skills if skills is not None else Skills(None)
        self.post_processors = (
            post_processors if post_processors is not None else PostProcessors(None)
        )
        self.settings = SettingsService(
            self,
            runtime_reader=runtime_settings_reader,
            runtime_updater=runtime_settings_updater,
            startup_validator=startup_validator,
        )
        self.assistant = AssistantService(self)
        self.maintenance = MaintenanceService(
            self,
            migration_inventory_reader=migration_inventory_reader,
            cleanup_executor=cleanup_executor,
        )
        self._schema_manager = schema_manager
        self._transaction_factory = transaction_factory
        detected_capabilities = set(capabilities or ())
        if database is not None:
            detected_capabilities.update(
                getattr(database, "capabilities", {Capabilities.TRANSACTIONS})
            )
        if object_store is not None:
            detected_capabilities.update(
                getattr(object_store, "capabilities", {Capabilities.OBJECT_STREAMING})
            )
        if graph_store is not None:
            detected_capabilities.update(graph_store.capabilities)
        if model_provider is not None:
            detected_capabilities.update(model_provider.capabilities)
        if job_backend is not None:
            detected_capabilities.update(job_backend.capabilities)
        if vector_search is not None:
            detected_capabilities.update(vector_search.capabilities)
        effective_capabilities = frozenset(detected_capabilities)
        self.parsers = (
            parser_registry_factory(self)
            if parser_registry_factory is not None
            else Parsers(self)
        )
        self.steps = steps if steps is not None else Steps()
        self.pipelines = (
            pipeline_registry_factory(self, effective_capabilities)
            if pipeline_registry_factory is not None
            else Pipelines(self, capabilities=effective_capabilities)
        )
        bind_job_backend = getattr(self.pipelines, "_bind_job_backend", None)
        if callable(bind_job_backend):
            bind_job_backend(job_backend)
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
        return cls._from_legacy_resources(
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
        graph_store: GraphStore | None = None,
        model_provider: ModelProvider | None = None,
        job_backend: JobBackend | None = None,
        vector_search: VectorSearch | None = None,
        parser_registry_factory: Callable[["KnowledgeBase"], Parsers] | None = None,
        pipeline_registry_factory: Callable[
            ["KnowledgeBase", frozenset[str]], Pipelines
        ]
        | None = None,
        steps: Steps | None = None,
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
            return cls._from_generic_resources(
                config=config,
                database=database,
                object_store=object_store,
                capabilities=capabilities,
                graph_store=graph_store,
                model_provider=model_provider,
                job_backend=job_backend,
                vector_search=vector_search,
                parser_registry_factory=parser_registry_factory,
                pipeline_registry_factory=pipeline_registry_factory,
                steps=steps,
            )
        except Exception:
            if object_store is not None:
                object_store.close()
            database.close()
            raise

    @classmethod
    def _from_legacy_resources(
        cls,
        *,
        config: MKBConfig,
        database: Database,
        object_store: ObjectStore | None,
        services: ServiceBindings | None = None,
        capabilities: frozenset[str] | None = None,
    ) -> "KnowledgeBase":
        from mkb.adapters import (
            InMemoryGraphStore,
            SQLAlchemyArtifactRepository,
            SQLAlchemyCollectionGroupRepository,
            SQLAlchemyCollectionRepository,
            SQLAlchemyExtractionSchemaRepository,
            SQLAlchemyFeedbackRepository,
            SQLAlchemyPostProcessorRepository,
            SQLAlchemyProjectionRepository,
            SQLAlchemyRecordRepository,
            SQLAlchemySourceRepository,
            SQLAlchemySkillRepository,
            SQLAlchemyWorkflowRepository,
        )
        from mkb import runtime_settings
        from mkb.agents.ontology_induction import run_ontology_induction
        from mkb.skills import registry as skill_registry
        from mkb.spaces.export_qa_bench import (
            export_projection_to_yaml,
            export_space_to_yaml,
        )
        from mkb.spaces.registry import load_space_from_file
        from mkb.config import settings as application_settings
        from mkb.maintenance import apply_retention, prune_job_history, retention_plan
        from mkb.migration_inventory import migration_inventory

        def import_skill_files(files):
            if len(files) == 1:
                filename, stream = files[0]
                if filename.lower().endswith(".zip"):
                    return skill_registry.create_skill_from_zip(filename, stream)
                return skill_registry.create_skill_from_single_file(filename, stream)
            return skill_registry.create_skill_from_files(files)

        def update_runtime_settings(updates):
            return runtime_settings.public_view(
                runtime_settings.update_settings(updates)
            )

        def execute_cleanup(*, older_than_days, job_days, apply, confirm):
            plan = retention_plan(older_than_days=older_than_days)
            result = {
                "local": plan,
                "jobs": prune_job_history(older_than_days=job_days),
            }
            if apply:
                result["local"] = apply_retention(plan, confirm=confirm)
                result["jobs"] = prune_job_history(
                    older_than_days=job_days, apply=True
                )
            return result

        knowledge_base = cls(
            services=services,
            config=config,
            database=database,
            object_store=object_store,
            graph_store=InMemoryGraphStore(),
            collections=Collections(
                SQLAlchemyCollectionRepository(database),
                CollectionGroups(SQLAlchemyCollectionGroupRepository(database)),
            ),
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
            feedback=Feedback(SQLAlchemyFeedbackRepository(database)),
            skills=Skills(
                SQLAlchemySkillRepository(database),
                importer=import_skill_files,
            ),
            post_processors=PostProcessors(
                SQLAlchemyPostProcessorRepository(database)
            ),
            runtime_settings_reader=runtime_settings.public_view,
            runtime_settings_updater=update_runtime_settings,
            startup_validator=application_settings.validate_startup,
            migration_inventory_reader=migration_inventory,
            cleanup_executor=execute_cleanup,
            materials=Materials(
                frames=MaterialFrames(services),
                spaces=MaterialSpaces(services, file_loader=load_space_from_file),
                projections=MaterialProjections(
                    services,
                    projection_exporter=export_projection_to_yaml,
                    space_exporter=export_space_to_yaml,
                ),
                workflows=MaterialWorkflows(
                    Workflows(SQLAlchemyWorkflowRepository(database)),
                    services,
                    schema_curator=run_ontology_induction,
                ),
                graph=MaterialGraph(services),
                feedback=MaterialFeedback(services),
                library=MaterialLibrary(services),
            ),
            capabilities=capabilities,
        )
        if services is not None and knowledge_base.graph is not None:
            knowledge_base.graph._bind_compatibility(
                loader=getattr(services, "get_knowledge_graph", None),
                extractor=getattr(services, "extract_knowledge_graph", None),
                reviewer=getattr(services, "review_knowledge_graph", None),
            )
        from mkb.builtin_pipelines import register_materials_builtin_pipelines

        register_materials_builtin_pipelines(knowledge_base)
        return knowledge_base

    @classmethod
    def _from_generic_resources(
        cls,
        *,
        config: MKBConfig,
        database: Database,
        object_store: ObjectStore | None,
        capabilities: frozenset[str] | None = None,
        graph_store: GraphStore | None = None,
        model_provider: ModelProvider | None = None,
        job_backend: JobBackend | None = None,
        vector_search: VectorSearch | None = None,
        parser_registry_factory: Callable[["KnowledgeBase"], Parsers] | None = None,
        pipeline_registry_factory: Callable[
            ["KnowledgeBase", frozenset[str]], Pipelines
        ]
        | None = None,
        steps: Steps | None = None,
    ) -> "KnowledgeBase":
        from mkb.adapters.generic_repositories import (
            GenericArtifactRepository,
            GenericCollectionGroupRepository,
            GenericCollectionRepository,
            GenericExtractionSchemaRepository,
            GenericEvidenceRepository,
            GenericFeedbackRepository,
            GenericJobBackend,
            GenericPostProcessorRepository,
            GenericProjectionRepository,
            GenericRecordRepository,
            GenericSchemaManager,
            GenericSkillRepository,
            GenericSourceRepository,
        )
        from mkb.adapters.graph import InMemoryGraphStore

        def transaction_factory(session) -> Transaction:
            rollback_actions: list[Callable[[], None]] = []
            transaction_evidence = EvidenceLinks(
                GenericEvidenceRepository(database, session)
            )
            transaction_sources = (
                Sources(
                    GenericSourceRepository(database, session),
                    object_store,
                    default_bucket=config.raw_bucket,
                    on_rollback=rollback_actions.append,
                )
                if object_store is not None
                else None
            )
            transaction_artifacts = (
                Artifacts(
                    GenericArtifactRepository(database, session),
                    object_store,
                    default_bucket=config.processed_bucket,
                    sources=transaction_sources,
                    on_rollback=rollback_actions.append,
                )
                if object_store is not None and transaction_sources is not None
                else None
            )
            return Transaction(
                collections=Collections(
                    GenericCollectionRepository(database, session),
                    CollectionGroups(
                        GenericCollectionGroupRepository(database, session)
                    ),
                ),
                records=Records(
                    GenericRecordRepository(database, session),
                    transaction_evidence,
                ),
                schemas=ExtractionSchemas(
                    GenericExtractionSchemaRepository(database, session)
                ),
                projections=Projections(
                    GenericProjectionRepository(database, session)
                ),
                evidence=transaction_evidence,
                sources=transaction_sources,
                artifacts=transaction_artifacts,
                _rollback_actions=rollback_actions,
            )

        sources = (
            Sources(
                GenericSourceRepository(database),
                object_store,
                default_bucket=config.raw_bucket,
            )
            if object_store is not None
            else None
        )
        effective_job_backend = (
            job_backend if job_backend is not None else GenericJobBackend(database)
        )
        evidence = EvidenceLinks(GenericEvidenceRepository(database))

        return cls(
            config=config,
            database=database,
            object_store=object_store,
            graph_store=graph_store if graph_store is not None else InMemoryGraphStore(),
            model_provider=model_provider,
            job_backend=effective_job_backend,
            vector_search=vector_search,
            collections=Collections(
                GenericCollectionRepository(database),
                CollectionGroups(GenericCollectionGroupRepository(database)),
            ),
            sources=sources,
            artifacts=(
                Artifacts(
                    GenericArtifactRepository(database),
                    object_store,
                    default_bucket=config.processed_bucket,
                    sources=sources,
                )
                if object_store is not None
                else None
            ),
            records=Records(GenericRecordRepository(database), evidence),
            schemas=ExtractionSchemas(GenericExtractionSchemaRepository(database)),
            projections=Projections(GenericProjectionRepository(database)),
            evidence=evidence,
            feedback=Feedback(GenericFeedbackRepository(database)),
            skills=Skills(GenericSkillRepository(database)),
            post_processors=PostProcessors(
                GenericPostProcessorRepository(database)
            ),
            capabilities=capabilities,
            schema_manager=GenericSchemaManager(database),
            transaction_factory=transaction_factory,
            parser_registry_factory=parser_registry_factory,
            pipeline_registry_factory=pipeline_registry_factory,
            steps=steps,
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
        if self.graph_store is not None:
            self.graph_store.close()
        if self.model_provider is not None:
            self.model_provider.close()
        if self.job_backend is not None:
            self.job_backend.close()
        if self.vector_search is not None:
            self.vector_search.close()
        if self.database is not None:
            self.database.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("KnowledgeBase is closed")

    def initialize(self) -> int:
        """Explicitly create missing SDK-owned tables without dropping existing data."""
        self._ensure_open()
        if self._schema_manager is None:
            raise ConflictError("This client does not manage a portable SDK schema")
        return self._schema_manager.initialize()

    def schema_version(self) -> int | None:
        """Return the initialized portable schema version, or ``None`` if absent."""
        self._ensure_open()
        if self._schema_manager is None:
            return None
        return self._schema_manager.version()

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """Open one relational transaction for SDK-managed repositories."""
        self._ensure_open()
        if self.database is None or self._transaction_factory is None:
            raise ConflictError("Transactions are unavailable for this client")
        transaction: Transaction | None = None
        try:
            with self.database.transaction() as session:
                transaction = self._transaction_factory(session)
                yield transaction
        except Exception:
            if transaction is not None:
                transaction._compensate()
            raise

    def _call(self, name: str, *args, **kwargs):
        self._ensure_open()
        operation = getattr(self._services, name)
        return operation(*args, **kwargs)

    # Current end-to-end lifecycle. These explicit methods are intentionally small;
    # domain-specific grouped APIs will be added without relying on __getattr__.
    def setup(self) -> None:
        return self._call("setup")

    def reset_database(self, *, confirm: str) -> None:
        """Reset legacy application tables only with an exact confirmation token."""
        if confirm != "RESET DATABASE":
            raise ValidationError("reset_database requires confirm='RESET DATABASE'")
        return self._call("reset_db")

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
