"""Use the explicit SDK boundary with the existing local MKB data."""

from mkb import KnowledgeBase


def main() -> None:
    # This reads the same .env/config.yaml and the same PostgreSQL/MinIO data as the
    # current CLI and React application. It does not migrate or re-extract anything.
    with KnowledgeBase.from_environment() as kb:
        if kb.database is not None:
            kb.database.check()
        if kb.object_store is not None:
            kb.object_store.check((kb.config.raw_bucket, kb.config.processed_bucket))

        # New typed SDK path. These are the existing research_projects rows presented
        # as generic collections through this client's injected database adapter.
        collections = kb.collections.list(limit=10) if kb.collections else []
        if collections:
            first = collections[0]
            print(first.model_dump(mode="json"))
            sources = (
                kb.sources.list(collection_id=first.id, limit=10) if kb.sources else []
            )
            if sources:
                source = sources[0]
                print({
                    "source_id": str(source.id),
                    "source_content_exists": kb.sources.content_exists(source.id),
                })
                artifacts = (
                    kb.artifacts.list(source_id=source.id, limit=10)
                    if kb.artifacts
                    else []
                )
                if artifacts:
                    artifact = artifacts[0]
                    print({
                        "artifact_id": str(artifact.id),
                        "artifact_content_exists": kb.artifacts.content_exists(artifact.id),
                    })

            record = kb.records.get_for_collection(first.id) if kb.records else None
            if record:
                print({
                    "record_id": str(record.id),
                    "record_status": record.status,
                    "record_json_bytes": len(record.model_dump_json().encode()),
                })

            projections = (
                kb.projections.list(collection_id=first.id, newest_only=True, limit=10)
                if kb.projections
                else []
            )
            if projections:
                projection = projections[0]
                schema = kb.schemas.get(projection.schema_id) if kb.schemas else None
                print({
                    "projection_id": str(projection.id),
                    "schema": schema.name if schema else str(projection.schema_id),
                    "projection_json_bytes": len(projection.model_dump_json().encode()),
                })

        projects = kb.list_projects(limit=10)
        print(f"Found {len(projects)} project(s)")
        if not projects:
            return

        project_id = projects[0]["project_id"]
        frame = kb.get_frame(project_id)
        print({"project_id": project_id, "has_frame": frame is not None})


if __name__ == "__main__":
    main()
