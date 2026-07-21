import hashlib

import pytest

from mkb import ConflictError, KnowledgeBase, ValidationError


def _fixture(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'repair.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    )
    kb.initialize()
    collection = kb.collections.create(name="Repair")
    source = kb.sources.add_text(collection.id, "source")
    content = b"verified recovered artifact"
    artifact = kb.artifacts.add_bytes(
        source.id,
        content,
        processing_type="TEXT",
        format="txt",
    )
    kb.object_store.delete(artifact.storage.bucket, artifact.storage.key)
    local = tmp_path / "recovered.txt"
    local.write_bytes(content)
    return kb, artifact, local, content


def test_missing_artifact_repair_is_dry_run_confirmed_and_idempotent(tmp_path):
    kb, artifact, local, content = _fixture(tmp_path)
    with kb:
        dry_run = kb.maintenance.restore_missing_artifact(artifact.id, local)
        assert dry_run.data["status"] == "ready"
        assert dry_run.data["sha256"] == hashlib.sha256(content).hexdigest()
        assert not kb.object_store.exists(artifact.storage.bucket, artifact.storage.key)

        with pytest.raises(ValidationError, match="RESTORE MISSING OBJECT"):
            kb.maintenance.restore_missing_artifact(artifact.id, local, apply=True)

        restored = kb.maintenance.restore_missing_artifact(
            artifact.id,
            local,
            apply=True,
            confirm="RESTORE MISSING OBJECT",
        )
        assert restored.data["status"] == "restored"
        assert kb.artifacts.read_bytes(artifact.id) == content

        repeated = kb.maintenance.restore_missing_artifact(
            artifact.id,
            local,
            apply=True,
            confirm="RESTORE MISSING OBJECT",
        )
        assert repeated.data["status"] == "already_present"
        assert repeated.data["applied"] is False


def test_missing_artifact_repair_rejects_wrong_local_content(tmp_path):
    kb, artifact, local, _content = _fixture(tmp_path)
    with kb:
        local.write_bytes(b"wrong")
        with pytest.raises(ConflictError, match="does not match"):
            kb.maintenance.restore_missing_artifact(artifact.id, local)
