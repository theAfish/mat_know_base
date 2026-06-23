"""Validated canonical graph and raw-to-canonical mapping contract."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from mkb.workflows.contract import RelationType
from mkb.workflows.schema_library import CANONICAL_SCHEMA_VERSION

CanonicalKind = Literal["object", "operation"]
ObjectSchema = Literal["MaterialObject", "MoleculeObject", "StructureObject", "PropertyObject", "DataObject"]


class CanonicalNode(BaseModel):
    node_id: str
    label: str = Field(min_length=1)
    node_kind: CanonicalKind
    object_schema: ObjectSchema | None = None
    operation_template_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_node_ids: list[str] = Field(min_length=1)


class CanonicalEdge(BaseModel):
    edge_id: str
    source_node: str
    target_node: str
    relation_type: RelationType
    raw_edge_ids: list[str] = Field(default_factory=list)


class RawCanonicalMapping(BaseModel):
    raw_node_ids: list[str] = Field(min_length=1)
    canonical_node_ids: list[str] = Field(min_length=1)
    mapping_type: Literal["one_to_one", "many_to_one", "one_to_many", "coarse_to_fine"]
    confidence: float = Field(ge=0, le=1)
    justification: str = Field(min_length=1)


class UnmatchedRawInformation(BaseModel):
    raw_node_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    preserved_data: dict[str, Any] = Field(default_factory=dict)


class CanonicalWorkflowGraph(BaseModel):
    schema_version: str = Field(default=CANONICAL_SCHEMA_VERSION, pattern=r"^workflow-schema/\d+\.\d+$")
    canonicalization_id: str
    paper_id: str
    raw_extraction_id: str
    nodes: list[CanonicalNode]
    edges: list[CanonicalEdge]
    raw_to_canonical_mappings: list[RawCanonicalMapping]
    unmatched_raw_information: list[UnmatchedRawInformation] = Field(default_factory=list)
    granularity_mappings: list[dict[str, Any]] = Field(default_factory=list)
    proposed_schema_updates: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "CanonicalWorkflowGraph":
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("canonical node IDs must be unique")
        for node in self.nodes:
            if node.node_kind == "object" and not node.object_schema:
                raise ValueError(f"object node {node.node_id} requires object_schema")
        for edge in self.edges:
            if edge.source_node not in node_ids or edge.target_node not in node_ids:
                raise ValueError(f"edge {edge.edge_id} references unknown canonical node")
        for mapping in self.raw_to_canonical_mappings:
            if not set(mapping.canonical_node_ids).issubset(node_ids):
                raise ValueError("mapping references unknown canonical node")
        return self
