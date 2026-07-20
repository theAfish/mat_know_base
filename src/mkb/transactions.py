"""Public transaction-scoped grouped services."""

from __future__ import annotations

from dataclasses import dataclass

from mkb.repositories import Collections, ExtractionSchemas, Projections, Records


@dataclass(frozen=True)
class Transaction:
    """Repository services sharing one relational database transaction."""

    collections: Collections
    records: Records
    schemas: ExtractionSchemas
    projections: Projections
