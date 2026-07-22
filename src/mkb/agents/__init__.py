"""Materials-agent entry points, loaded only when the materials extra is installed.

The portable SDK imports :mod:`mkb.agents.runtime` to bind client-owned resources.
Keep this package initializer free of ADK imports so ``import mkb`` remains usable
with the base package.  The public agent helpers retain their historical import path
and load their optional dependencies only when a caller requests them.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ALL_TOOLS",
    "AgentRunner",
    "RunResult",
    "build_extraction_agent",
    "run_extraction",
    "run_extraction_all",
]


def __getattr__(name: str) -> Any:
    """Lazily resolve ADK-backed agent helpers from their supported import path."""
    if name in {"build_extraction_agent", "run_extraction", "run_extraction_all"}:
        from mkb.agents.extraction import (
            build_extraction_agent,
            run_extraction,
            run_extraction_all,
        )

        return {
            "build_extraction_agent": build_extraction_agent,
            "run_extraction": run_extraction,
            "run_extraction_all": run_extraction_all,
        }[name]
    if name in {"AgentRunner", "RunResult"}:
        from mkb.agents.runner import AgentRunner, RunResult

        return {"AgentRunner": AgentRunner, "RunResult": RunResult}[name]
    if name == "ALL_TOOLS":
        from mkb.agents.tools import ALL_TOOLS

        return ALL_TOOLS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
