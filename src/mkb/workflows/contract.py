"""Card-based, evidence-grounded paper workflow contract.

The v2 contract deliberately keeps the historic ``Raw*`` class names and
fields readable.  Existing immutable extractions therefore remain valid while
new extractions persist reusable card names and run-specific detail directly,
without a second per-paper canonicalization pass.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RAW_WORKFLOW_SCHEMA_VERSION = "workflow-cards/2.0"
LEGACY_RAW_WORKFLOW_SCHEMA_VERSION = "raw-workflow/1.0"
EXTRACTOR_VERSION = "workflow-extractor/2.0"

NodeKind = Literal["object", "operation", "planning", "reasoning", "unknown"]
RelationType = Literal[
    "input_to", "produces", "same_as", "part_of", "has_part",
    "expands_to", "summarized_by", "motivates", "leads_to",
]


class PaperLocation(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset_id: str | None = None
    section: str | None = None
    page: int | None = Field(default=None, ge=1)
    paragraph: str | None = None
    figure_or_table: str | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)


class ParameterValue(BaseModel):
    """A reproducible value that does not need a globally predeclared slot."""

    model_config = ConfigDict(extra="allow")

    value: Any
    unit: str | None = None
    uncertainty: Any | None = None
    source: Literal["explicit", "derived", "inferred"] = "explicit"
    evidence_text: str | None = None


class RawWorkflowNode(BaseModel):
    """An instantiated Object or Operation card.

    ``canonical_name`` is the reusable concept (for example ``XRD
    Measurement``); all paper/run-specific information belongs in the open
    structured fields. ``card_id`` is optional until ontology induction maps
    the instance to a versioned ontology card.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    canonical_name: str | None = Field(default=None, min_length=1)
    card_id: str | None = None
    node_kind: NodeKind | None = None
    semantic_type: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    identity: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    role: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    unparsed_modifiers: list[str] = Field(default_factory=list)
    aliases_observed: list[str] = Field(default_factory=list)
    ontology_status: Literal["matched", "candidate", "unmapped"] = "unmapped"

    # Evidence/provenance stays on every instance. These compatibility fields
    # also make all previously stored v1 graphs readable.
    raw_name: str = Field(min_length=1)
    node_kind_guess: NodeKind
    attributes_explicitly_mentioned: dict[str, Any] = Field(default_factory=dict)
    evidence_text: str = Field(min_length=1)
    paper_location: PaperLocation
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_node(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.setdefault("canonical_name", data.get("short_name_guess") or data.get("raw_name"))
        data.setdefault("node_kind", data.get("node_kind_guess"))
        data.setdefault("semantic_type", data.get("node_category_guess") or data.get("node_kind_guess"))
        data.setdefault("parameters", data.get("parameter_fields") or data.get("attributes_explicitly_mentioned") or {})
        data.setdefault("identity", data.get("identity_fields") or {})
        data.setdefault("state", data.get("state_fields") or {})
        data.setdefault("role", data.get("role_fields") or {})
        data.setdefault("context", data.get("context_fields") or {})
        modifiers = data.get("unparsed_modifiers", [])
        if isinstance(modifiers, dict):
            modifiers = [f"{key}: {item}" for key, item in modifiers.items()]
        elif isinstance(modifiers, str):
            modifiers = [modifiers]
        data["unparsed_modifiers"] = modifiers
        # Consume the transitional prompt-only field names rather than silently
        # dropping them (the original source of lost reproducibility data).
        for key in (
            "short_name_guess", "node_category_guess", "parameter_fields",
            "identity_fields", "state_fields", "role_fields", "context_fields",
        ):
            data.pop(key, None)
        return data

    @model_validator(mode="after")
    def validate_card(self) -> "RawWorkflowNode":
        if self.node_kind != self.node_kind_guess:
            raise ValueError("node_kind and node_kind_guess must agree")
        return self


class RawWorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    source_node: str
    target_node: str
    relation_type: RelationType
    input_role: str | None = None
    output_role: str | None = None
    ordinal: int | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_text: str = Field(min_length=1)
    paper_location: PaperLocation | None = None
    confidence: float = Field(ge=0, le=1)


class ReproducibilityAssessment(BaseModel):
    level: Literal["complete", "approximate", "insufficient", "not_applicable"] = "approximate"
    missing_details: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    notes: str | None = None


class RawWorkflowGraph(BaseModel):
    """One paper's evidence layer, composed of instantiated reusable cards."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["workflow-cards/2.0", "raw-workflow/1.0"] = RAW_WORKFLOW_SCHEMA_VERSION
    ontology_version: str | None = None
    paper_id: str
    extraction_id: str
    nodes: list[RawWorkflowNode]
    edges: list[RawWorkflowEdge]
    reproducibility: ReproducibilityAssessment = Field(default_factory=ReproducibilityAssessment)
    unresolved_information: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "RawWorkflowGraph":
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node_id values must be unique")
        if len({edge.edge_id for edge in self.edges}) != len(self.edges):
            raise ValueError("edge_id values must be unique")
        by_id = {node.node_id: node for node in self.nodes}
        for edge in self.edges:
            if edge.source_node not in by_id or edge.target_node not in by_id:
                raise ValueError(f"edge {edge.edge_id} references an unknown node")
            source, target = by_id[edge.source_node], by_id[edge.target_node]
            if edge.relation_type == "input_to" and not (
                source.node_kind == "object" and target.node_kind == "operation"
            ):
                raise ValueError("input_to must connect object -> operation")
            if edge.relation_type == "produces" and not (
                source.node_kind == "operation" and target.node_kind == "object"
            ):
                raise ValueError("produces must connect operation -> object")
            if edge.relation_type in {"motivates", "leads_to"} and source.node_kind not in {
                "planning", "reasoning",
            }:
                raise ValueError(f"{edge.relation_type} must start from planning or reasoning")
        return self


# Intent-revealing v2 names. Raw* aliases remain the persistence/API compatibility
# surface while callers migrate.
WorkflowCardInstance = RawWorkflowNode
WorkflowCardEdge = RawWorkflowEdge
WorkflowGraph = RawWorkflowGraph


ID_CONVENTIONS = {
    "paper": "project UUID (one project is one paper package)",
    "extraction": "UUID",
    "node_instance": "raw:<extraction UUID>:n<zero-padded integer>",
    "edge_instance": "raw:<extraction UUID>:e<zero-padded integer>",
    "ontology_card": "card:<ontology version>:<object|operation|planning|reasoning>:<slug>",
}
