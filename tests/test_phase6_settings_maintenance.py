from mkb import EffectiveSettings, KnowledgeBase, MaintenanceReport


def test_effective_settings_and_backup_metadata_never_expose_secrets(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'settings.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
        object_store_access_key="access-secret",
        object_store_secret_key="object-secret",
    )
    with kb:
        settings = kb.settings.inspect()
        backup = kb.maintenance.backup_metadata()

        assert isinstance(settings, EffectiveSettings)
        assert settings.database_backend == "sqlite"
        assert settings.buckets["raw"] == "raw"
        rendered = backup.model_dump_json()
        assert "access-secret" not in rendered
        assert "object-secret" not in rendered
        assert str(tmp_path / "settings.db") not in rendered


def test_inventory_reconciliation_and_cleanup_plan_are_read_only(tmp_path):
    kb = KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'maintenance.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    )
    with kb:
        kb.initialize()
        collection = kb.collections.create(name="Maintenance")
        source = kb.sources.add_text(collection.id, "content")

        inventory = kb.maintenance.inventory()
        healthy = kb.maintenance.reconcile()
        plan = kb.maintenance.cleanup_plan(roots=[tmp_path / "cleanup-candidates"])

        assert isinstance(inventory, MaintenanceReport)
        assert inventory.data["resource_counts"]["collections"] == 1
        assert inventory.data["resource_counts"]["sources"] == 1
        assert healthy.ok is True
        assert plan.data["dry_run"] is True

        kb.object_store.delete(source.storage.bucket, source.storage.key)
        broken = kb.maintenance.reconcile()
        assert broken.ok is False
        assert broken.data["missing_objects"][0]["resource_id"] == str(source.id)
