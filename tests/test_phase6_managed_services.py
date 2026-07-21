import pytest

from mkb import ConflictError, KnowledgeBase, NotFoundError


def _client(tmp_path):
    return KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'managed.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    )


def test_feedback_lifecycle_is_typed_and_persistent(tmp_path):
    with _client(tmp_path) as kb:
        assert kb.initialize() == 6
        collection = kb.collections.create(name="Feedback")
        record = kb.records.create(collection_id=collection.id, data={"value": 1})

        item = kb.feedback.create(
            target_record_id=record.id,
            target_collection_id=collection.id,
            category="ambiguous_data",
            question="Which unit applies?",
        )
        reviewing = kb.feedback.review(item.id)
        resolved = kb.feedback.resolve(
            item.id,
            notes="The source specifies kelvin.",
            resolved_by="reviewer",
        )

        assert reviewing.status == "IN_REVIEW"
        assert resolved.status == "RESOLVED"
        assert resolved.resolved_at is not None
        assert kb.feedback.list(collection_id=collection.id) == [resolved]
        with pytest.raises(ConflictError, match="terminal"):
            kb.feedback.review(item.id)


def test_skill_and_post_processor_registration_are_client_owned(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'extensions.db'}"
    object_store_url = (tmp_path / "objects").as_uri()
    with KnowledgeBase.from_url(
        database_url=database_url,
        object_store_url=object_store_url,
    ) as kb:
        kb.initialize()
        skill = kb.skills.create(
            name="Normalize Units",
            content="# Normalize Units\nConvert measurements to SI.",
        )
        processor = kb.post_processors.register(
            name="Choose projection",
            source="print('{}')\n",
        )
        assert kb.skills.get("normalize-units") == skill
        assert kb.post_processors.get(processor.id) == processor

    with KnowledgeBase.from_url(
        database_url=database_url,
        object_store_url=object_store_url,
    ) as reopened:
        assert reopened.skills.get(skill.id) == skill
        assert reopened.post_processors.get(processor.id) == processor
        reopened.skills.delete(skill.id)
        reopened.post_processors.delete(processor.id)
        with pytest.raises(NotFoundError):
            reopened.skills.require(skill.id)
        with pytest.raises(NotFoundError):
            reopened.post_processors.require(processor.id)
