"""
Dev Agent interface — design only (TODO 6).

This agent analyzes feedback items classified as DEV_ISSUE and determines
whether problems are prompt issues, tool limitations, schema issues, or
agent reasoning failures. It can suggest changes to prompts, space
definitions, and tool implementations.

NOT IMPLEMENTED — this file defines the interface for future implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DevRecommendation:
    """A recommendation from the dev agent about a system-level issue."""
    feedback_id: str
    classification: str  # "prompt_issue", "tool_limitation", "schema_issue", "agent_reasoning"
    recommendation: str
    suggested_changes: dict[str, Any] = field(default_factory=dict)
    priority: str = "medium"  # "low", "medium", "high", "critical"


@dataclass
class PromptPatch:
    """A suggested modification to an agent prompt."""
    agent_type: str  # "extraction", "review", "projection"
    section: str  # which section of the prompt to modify
    current_text: str
    suggested_text: str
    reason: str


@dataclass
class SpacePatch:
    """A suggested modification to a space definition."""
    space_id: str
    field: str  # "extraction_schema", "system_prompt", "field_descriptions"
    current_value: Any
    suggested_value: Any
    reason: str


class DevAgentInterface:
    """Interface for the dev agent that analyzes system-level issues.

    The dev agent reads feedback items with status DEV_ISSUE and produces
    recommendations for improving the system (prompts, tools, schemas).

    This is activated by the user, not automatically.
    """

    def analyze_feedback(self, feedback_items: list[dict]) -> list[DevRecommendation]:
        """Classify each DEV_ISSUE feedback item and produce recommendations.

        Args:
            feedback_items: List of feedback dicts with status=DEV_ISSUE.

        Returns:
            List of DevRecommendation with classification and suggestions.
        """
        raise NotImplementedError("Dev agent not yet implemented")

    def suggest_prompt_changes(
        self, agent_type: str, issues: list[DevRecommendation]
    ) -> list[PromptPatch]:
        """Suggest modifications to agent prompts based on patterns in feedback.

        Args:
            agent_type: Which agent's prompt to improve ("extraction", "review", "projection").
            issues: Related DevRecommendations classified as prompt_issue.

        Returns:
            List of PromptPatch with specific text changes.
        """
        raise NotImplementedError("Dev agent not yet implemented")

    def suggest_space_changes(
        self, space: dict, issues: list[DevRecommendation]
    ) -> list[SpacePatch]:
        """Suggest modifications to a space definition.

        Args:
            space: Space dict from api.get_space().
            issues: Related DevRecommendations classified as schema_issue.

        Returns:
            List of SpacePatch with specific changes.
        """
        raise NotImplementedError("Dev agent not yet implemented")
