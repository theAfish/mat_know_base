"""Use the explicit SDK boundary with the existing local MKB data."""

from mkb import KnowledgeBase


def main() -> None:
    # This reads the same .env/config.yaml and the same PostgreSQL/MinIO data as the
    # current CLI and React application. It does not migrate or re-extract anything.
    with KnowledgeBase.from_environment() as kb:
        projects = kb.list_projects(limit=10)
        print(f"Found {len(projects)} project(s)")
        if not projects:
            return

        project_id = projects[0]["project_id"]
        frame = kb.get_frame(project_id)
        print({"project_id": project_id, "has_frame": frame is not None})


if __name__ == "__main__":
    main()
