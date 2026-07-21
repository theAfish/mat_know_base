"""Materials-science extension services layered on the generic SDK."""

from __future__ import annotations

from dataclasses import dataclass

from mkb.repositories import Workflows


@dataclass(frozen=True)
class Materials:
    """Materials-specific services that preserve legacy application concepts."""

    workflows: Workflows | None = None
