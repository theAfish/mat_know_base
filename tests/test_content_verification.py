from mkb import KnowledgeBase


def test_content_verification_checks_references_and_deterministic_hashes(tmp_path):
    with KnowledgeBase.from_url(
        database_url=f"sqlite:///{tmp_path / 'verify.db'}",
        object_store_url=(tmp_path / "objects").as_uri(),
    ) as kb:
        kb.initialize()
        collection = kb.collections.create(name="Verify")
        source = kb.sources.add_text(collection.id, "source content")
        artifact = kb.artifacts.add_bytes(
            source.id,
            b"artifact content",
            processing_type="TEXT",
            format="txt",
        )

        report = kb.maintenance.verify_content(sample_size=10)
        assert report.ok is True
        assert report.data["sampled_count"] == 2
        assert report.data["mismatch_count"] == 0

        kb.object_store.delete(artifact.storage.bucket, artifact.storage.key)
        report = kb.maintenance.verify_content(sample_size=10)
        assert report.ok is False
        assert report.data["missing_count"] == 1
