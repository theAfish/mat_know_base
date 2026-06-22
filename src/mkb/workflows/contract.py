"""Phase-0 contract for faithful paper-level workflow graphs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RAW_WORKFLOW_SCHEMA_VERSION = "raw-workflow/1.0"
EXTRACTOR_VERSION = "workflow-extractor/1.0"

NodeKind = Literal["object", "operation", "unknown"]
RelationType = Literal[
    "input_to", "produces", "same_as", "part_of", "has_part",
    "expands_to", "summarized_by",
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


class RawWorkflowNode(BaseModel):
    node_id: str
    raw_name: str = Field(min_length=1)
    node_kind_guess: NodeKind
    attributes_explicitly_mentioned: dict[str, Any] = Field(default_factory=dict)
    evidence_text: str = Field(min_length=1)
    paper_location: PaperLocation
    confidence: float = Field(ge=0, le=1)


class RawWorkflowEdge(BaseModel):
    edge_id: str
    source_node: str
    target_node: str
    relation_type: RelationType
    evidence_text: str = Field(min_length=1)
    paper_location: PaperLocation | None = None
    confidence: float = Field(ge=0, le=1)


class RawWorkflowGraph(BaseModel):
    schema_version: Literal["raw-workflow/1.0"] = RAW_WORKFLOW_SCHEMA_VERSION
    paper_id: str
    extraction_id: str
    nodes: list[RawWorkflowNode]
    edges: list[RawWorkflowEdge]

    @model_validator(mode="after")
    def validate_graph(self) -> "RawWorkflowGraph":
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node_id values must be unique")
        if len({edge.edge_id for edge in self.edges}) != len(self.edges):
            raise ValueError("edge_id values must be unique")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source_node not in known or edge.target_node not in known:
                raise ValueError(f"edge {edge.edge_id} references an unknown node")
            source = next(node for node in self.nodes if node.node_id == edge.source_node)
            target = next(node for node in self.nodes if node.node_id == edge.target_node)
            if edge.relation_type == "input_to" and not (
                source.node_kind_guess == "object" and target.node_kind_guess == "operation"
            ):
                raise ValueError("input_to must connect object -> operation")
            if edge.relation_type == "produces" and not (
                source.node_kind_guess == "operation" and target.node_kind_guess == "object"
            ):
                raise ValueError("produces must connect operation -> object")
        return self


ID_CONVENTIONS = {
    "paper": "project UUID (one project is one paper package)",
    "raw_extraction": "UUID",
    "raw_node": "raw:<extraction UUID>:n<zero-padded integer>",
    "raw_edge": "raw:<extraction UUID>:e<zero-padded integer>",
    "canonical_node": "canonical:<schema version>:<UUID> (reserved for Phase 2)",
    "operation_template": "operation-template:<schema version>:<slug> (reserved for Phase 2)",
    "workflow_template": "workflow-template:<schema version>:<UUID> (reserved for Phase 4)",
}
