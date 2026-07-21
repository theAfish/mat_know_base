import uuid
from datetime import datetime, timezone

import mkb

from mkb import (
    Artifact,
    BackendUnavailableError,
    CacheKeyComponents,
    Collection,
    CollectionGroup,
    ConflictError,
    Entity,
    Evidence,
    EffectiveSettings,
    ExtractionSchema,
    FeedbackItem,
    GraphResult,
    GraphReview,
    Job,
    MaintenanceReport,
    MKBConfig,
    MKBError,
    NotFoundError,
    OperationReceipt,
    Page,
    PipelineExecutionError,
    Projection,
    PostProcessor,
    ProviderError,
    Record,
    Relation,
    Source,
    Skill,
    StorageReference,
    ValidationError,
    WorkflowRecord,
)


def test_root_public_boundary_excludes_persistence_session_interfaces():
    assert "Database" not in mkb.__all__
    assert "ObjectStore" not in mkb.__all__
    assert "GraphStore" not in mkb.__all__
    assert "ObjectInfo" not in mkb.__all__
    assert all(name == "__version__" or not name.startswith("_") for name in mkb.__all__)

    # Temporary explicit-import compatibility remains while adapters stabilize.
    assert mkb.Database is not None
    assert mkb.ObjectStore is not None


def test_public_exception_hierarchy_is_stable():
    for error_type in (
        NotFoundError,
        ConflictError,
        ValidationError,
        BackendUnavailableError,
        ProviderError,
        PipelineExecutionError,
    ):
        assert issubclass(error_type, MKBError)


def test_config_repr_does_not_disclose_credentials():
    config = MKBConfig(
        database_url="postgresql://user:database-secret@example.test/db",
        object_store_access_key="access-secret",
        object_store_secret_key="object-secret",
    )

    rendered = repr(config)

    assert "database-secret" not in rendered
    assert "access-secret" not in rendered
    assert "object-secret" not in rendered


def test_remaining_public_contract_models_serialize_as_json():
    now = datetime.now(timezone.utc)
    collection = Collection(id=uuid.uuid4(), name="Example")
    job = Job(id=uuid.uuid4(), kind="pipeline", status="QUEUED", created_at=now)
    page = Page[Collection](items=(collection,), limit=25, offset=0, total=1)
    receipt = OperationReceipt(
        operation="collection.create",
        status="COMPLETED",
        resource_type="collection",
        resource_id=collection.id,
        created_at=now,
    )

    assert job.model_dump(mode="json")["id"] == str(job.id)
    assert page.model_dump(mode="json")["items"][0]["id"] == str(collection.id)
    assert receipt.model_dump(mode="json")["resource_id"] == str(collection.id)


def test_all_domain_models_expose_json_serialization():
    model_types = (
        Artifact,
        CacheKeyComponents,
        Collection,
        CollectionGroup,
        Entity,
        Evidence,
        EffectiveSettings,
        ExtractionSchema,
        FeedbackItem,
        GraphResult,
        GraphReview,
        Job,
        MaintenanceReport,
        OperationReceipt,
        Page,
        Projection,
        PostProcessor,
        Record,
        Relation,
        Source,
        Skill,
        StorageReference,
        WorkflowRecord,
    )

    assert all(callable(getattr(model_type, "model_dump", None)) for model_type in model_types)
