"""Ingest API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    Path,
    uuid,
)


def ingest(
    directory: str | Path,
    label: str | None = None,
    *,
    user_named: bool = False,
) -> dict:
    """Ingest a single project directory.

    Creates or updates a ResearchProject record keyed on the directory path,
    then ingests any new files found inside it.

    ``user_named`` controls whether the provided label should be treated as a
    user-given name (in which case the project will be marked as such and
    excluded from later automatic renaming during extraction).

    Returns a summary dict with counts (total, ingested, duplicates, errors).
    """
    from mkb.ingest.worker import ingest_directory

    return ingest_directory(directory, label=label, user_named=user_named)

def sync(root_dir: str | Path) -> dict:
    """Sync all project subfolders under *root_dir*.

    Each immediate subdirectory of *root_dir* is treated as one research
    project.  New subfolders are registered as new projects; existing projects
    are scanned for new files.

    Returns a summary dict with per-project results.
    """
    from mkb.ingest.worker import sync_root

    return sync_root(root_dir)

def sync_project(project_id: str | uuid.UUID) -> dict:
    """Re-scan a single project's source directory for new files.

    Returns a summary dict with counts of newly ingested files.
    """
    from mkb.ingest.worker import sync_project as _sync_project

    pid = uuid.UUID(str(project_id))
    return _sync_project(pid)


# ── Processing ───────────────────────────────────────────────────

