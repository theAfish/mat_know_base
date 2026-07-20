"""Public transaction-scoped grouped services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mkb.repositories import (
    Artifacts,
    Collections,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
)


@dataclass(frozen=True)
class Transaction:
    """Repository services sharing one relational database transaction."""

    collections: Collections
    records: Records
    schemas: ExtractionSchemas
    projections: Projections
    sources: Sources | None = None
    artifacts: Artifacts | None = None
    _rollback_actions: list[Callable[[], None]] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )

    def _add_rollback_action(self, action: Callable[[], None]) -> None:
        """Register best-effort compensation for a non-transactional side effect."""
        self._rollback_actions.append(action)

    def _compensate(self) -> None:
        """Run compensations in reverse write order without masking the DB failure."""
        for action in reversed(self._rollback_actions):
            try:
                action()
            except Exception:
                # Reconciliation reports any object a backend could not remove.
                pass
        self._rollback_actions.clear()
