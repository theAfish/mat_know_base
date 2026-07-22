"""Time-boxed, read-only access to retired canonical-workflow records.

Removal target: 2026-10-31. No mutations or agent launch paths may be added here.
"""

from __future__ import annotations

COMPATIBILITY_READS_END = "2026-10-31"


def list_canonical_workflows(project_id, **kwargs):
    from mkb.services.compatibility_resources import workflow_operations

    return workflow_operations().list_canonical_workflows(project_id, **kwargs)


def get_canonical_workflow(project_id, **kwargs):
    from mkb.services.compatibility_resources import workflow_operations

    return workflow_operations().get_canonical_workflow(project_id, **kwargs)

__all__ = ["COMPATIBILITY_READS_END", "get_canonical_workflow", "list_canonical_workflows"]
