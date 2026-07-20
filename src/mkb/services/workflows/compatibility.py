"""Time-boxed, read-only access to retired canonical-workflow records.

Removal target: 2026-10-31. No mutations or agent launch paths may be added here.
"""

from __future__ import annotations

from mkb.services.workflows.legacy_canonicalization import (
    get_canonical_workflow,
    list_canonical_workflows,
)

COMPATIBILITY_READS_END = "2026-10-31"

__all__ = ["COMPATIBILITY_READS_END", "get_canonical_workflow", "list_canonical_workflows"]
