from mkb import KnowledgeBase


def test_material_project_rename_uses_typed_collections_without_legacy_services(tmp_path):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'projects.db'}") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Before")

        result = kb.materials.projects.rename(str(collection.id), "After")

        assert result == {
            "project_id": str(collection.id),
            "label": "After",
            "user_named": True,
        }
        updated = kb.collections.require(collection.id)
        assert updated.name == "After"
        assert updated.metadata["user_named"] is True


def test_material_project_groups_use_typed_collection_groups(tmp_path):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'groups.db'}") as kb:
        kb.initialize()
        first = kb.collections.create(name="First")
        second = kb.collections.create(name="Second")

        created = kb.materials.projects.create_group("Research", color="#123456")
        group_id = created["group_id"]
        assert created["project_count"] == 0

        assigned = kb.materials.projects.assign_group([str(first.id), str(second.id)], group_id)
        assert assigned == {"updated": 2, "group_id": group_id}
        assert kb.materials.projects.list_groups()[0]["project_count"] == 2

        updated = kb.materials.projects.update_group(group_id, description="  curated  ")
        assert updated["description"] == "curated"

        deleted = kb.materials.projects.delete_group(group_id)
        assert deleted == {
            "group_id": group_id,
            "deleted": True,
            "unassigned_projects": 2,
        }


def test_material_project_assets_use_typed_sources(tmp_path):
    with KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'assets.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    ) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Project")
        source = kb.sources.add_text(
            collection.id,
            "content",
            filename="notes.txt",
        )

        assert kb.materials.projects.list_assets(project_id=str(collection.id)) == [
            {
                "asset_id": str(source.id),
                "filename": "notes.txt",
                "mime_type": "text/plain; charset=utf-8",
                "size_bytes": 7,
                "status": "STORED",
            }
        ]


def test_material_frames_use_typed_records(tmp_path):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'frames.db'}") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Project")
        record = kb.records.create(
            collection_id=collection.id,
            data={"material": "calcite"},
            summary="A frame",
            annotations={"reviewed": True},
        )

        frame = kb.materials.frames.get(str(collection.id))

        assert frame["frame_id"] == str(record.id)
        assert frame["content"] == {"material": "calcite"}
        assert frame["agent_annotations"] == {"reviewed": True}


def test_material_spaces_use_typed_schema_registry(tmp_path):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'spaces.db'}") as kb:
        kb.initialize()
        created = kb.materials.spaces.create(
            name="Catalysis",
            domain="chemistry",
            extraction_schema={"type": "object"},
            system_prompt="Extract structured data.",
        )

        space = kb.materials.spaces.get(created["space_id"])
        assert space["name"] == "Catalysis"
        assert space["extraction_schema"] == {"type": "object"}

        updated = kb.materials.spaces.update(created["space_id"], purpose="freeform")
        assert updated["version"] == 2
        assert kb.materials.spaces.delete(created["space_id"]) == {
            "ok": True,
            "deleted": "Catalysis",
        }


def test_material_feedback_uses_typed_feedback_service(tmp_path):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'feedback.db'}") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Project")
        record = kb.records.create(collection_id=collection.id, data={})
        item = kb.feedback.create(
            target_record_id=record.id,
            target_collection_id=collection.id,
            category="missing_value",
            question="Which precursor was used?",
        )

        assert kb.materials.feedback.summary(str(collection.id))["total"] == 1
        resolved = kb.materials.feedback.resolve(
            feedback_id=str(item.id),
            status="RESOLVED",
            notes="Confirmed in the supplement.",
        )
        assert resolved == {"feedback_id": str(item.id), "status": "RESOLVED"}


def test_material_projection_reads_use_typed_projection_service(tmp_path):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'projections.db'}") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Project")
        record = kb.records.create(collection_id=collection.id, data={})
        schema = kb.schemas.create(
            name="Schema",
            domain="chemistry",
            definition={"type": "object"},
            system_prompt="Extract.",
        )
        projection = kb.projections.create(
            schema_id=schema.id,
            record_id=record.id,
            data={"material": "calcite"},
        )

        row = kb.materials.projections.get(str(projection.id))
        assert row["data"] == {"material": "calcite"}
        assert kb.materials.projections.list(space_id=str(schema.id))[0]["projection_id"] == str(
            projection.id
        )
