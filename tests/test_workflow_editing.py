import uuid
from types import SimpleNamespace

from mkb.workflows.contract import RAW_WORKFLOW_SCHEMA_VERSION, RawWorkflowGraph
from mkb.workflows.editing import normalize_raw_graph_payload


def _row(project_id: uuid.UUID):
    return SimpleNamespace(schema_version=RAW_WORKFLOW_SCHEMA_VERSION, project_id=project_id)


def test_normalize_raw_graph_payload_infers_relation_from_endpoint_kinds():
    eid = uuid.uuid4()
    payload, normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {"raw_name": "powder", "node_kind": "object", "evidence_text": "powder"},
                {"raw_name": "anneal", "node_kind": "operation", "evidence_text": "anneal"},
                {"raw_name": "sample", "node_kind": "object", "evidence_text": "sample"},
            ],
            "edges": [
                {
                    "source": "powder",
                    "target": "anneal",
                    "evidence_text": "powder was annealed",
                },
                {
                    "source": "anneal",
                    "target": "sample",
                    "evidence_text": "annealing produced sample",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert [edge.relation_type for edge in graph.edges] == ["input_to", "produces"]
    assert normalization["inferred_edge_relations"] == 2


def test_normalize_raw_graph_payload_accepts_canonical_node_shape_without_duplicates():
    eid = uuid.uuid4()
    payload, _normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {
                    "canonical_name": "Precursor Powder",
                    "node_kind": "object",
                    "evidence_text": "The precursor powder was annealed.",
                },
                {
                    "canonical_name": "Annealing",
                    "node_kind": "operation",
                    "evidence_text": "The precursor powder was annealed.",
                },
            ],
            "edges": [
                {
                    "source": "Precursor Powder",
                    "target": "Annealing",
                    "evidence_text": "The precursor powder was annealed.",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert graph.nodes[0].canonical_name == "Precursor Powder"
    assert graph.nodes[0].raw_name == "Precursor Powder"
    assert graph.nodes[0].node_kind_guess == "object"
    assert graph.edges[0].relation_type == "input_to"


def test_normalize_raw_graph_payload_overrides_wrong_workflow_relation():
    eid = uuid.uuid4()
    payload, normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {"raw_name": "powder", "node_kind": "object", "evidence_text": "powder"},
                {"raw_name": "anneal", "node_kind": "operation", "evidence_text": "anneal"},
            ],
            "edges": [
                {
                    "source": "powder",
                    "target": "anneal",
                    "relation_type": "produces",
                    "evidence_text": "powder was annealed",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert graph.edges[0].relation_type == "input_to"
    assert normalization["overrode_edge_relations"] == 1


def test_normalize_raw_graph_payload_infers_planning_relation():
    eid = uuid.uuid4()
    payload, normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {
                    "raw_name": "screen stable phases",
                    "node_kind": "planning",
                    "evidence_text": "screen stable phases",
                },
                {"raw_name": "anneal", "node_kind": "operation", "evidence_text": "anneal"},
            ],
            "edges": [
                {
                    "source": "screen stable phases",
                    "target": "anneal",
                    "relation_type": "input_to",
                    "evidence_text": "screening motivated annealing",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert graph.edges[0].relation_type == "motivates"
    assert normalization["overrode_edge_relations"] == 1


def test_normalize_raw_graph_payload_preserves_structural_relation():
    eid = uuid.uuid4()
    payload, _normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {
                    "raw_name": "phase diagram",
                    "node_kind": "object",
                    "evidence_text": "phase diagram",
                },
                {
                    "raw_name": "stability region",
                    "node_kind": "object",
                    "evidence_text": "stability region",
                },
            ],
            "edges": [
                {
                    "source": "stability region",
                    "target": "phase diagram",
                    "relation_type": "part_of",
                    "evidence_text": "the region is part of the phase diagram",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert graph.edges[0].relation_type == "part_of"


def test_normalize_raw_graph_payload_accepts_endpoint_aliases_and_numeric_refs():
    eid = uuid.uuid4()
    payload, normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {"id": "node-a", "name": "powder", "kind": "object", "evidence": "powder"},
                {"id": "node-b", "name": "anneal", "kind": "operation", "evidence": "anneal"},
                {"id": "node-c", "name": "sample", "kind": "object", "evidence": "sample"},
            ],
            "edges": [
                {"from_node": 1, "to_node": 2, "relation": "connection"},
                {"from": {"id": "node-b"}, "to": {"name": "sample"}, "kind": "edge"},
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert [edge.relation_type for edge in graph.edges] == ["input_to", "produces"]
    assert all(edge.evidence_text for edge in graph.edges)
    assert normalization["inferred_edge_relations"] == 0
    assert normalization["overrode_edge_relations"] == 2


def test_normalize_raw_graph_payload_rewrites_invalid_relation_when_endpoints_are_known():
    eid = uuid.uuid4()
    payload, normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {"raw_name": "screen stable phases", "node_kind": "reasoning", "evidence_text": "screen"},
                {"raw_name": "anneal", "node_kind": "operation", "evidence_text": "anneal"},
            ],
            "edges": [
                {
                    "source_label": "screen stable phases",
                    "target_label": "anneal",
                    "relation_type": "depends_on",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert graph.edges[0].relation_type == "motivates"
    assert graph.edges[0].evidence_text == "screen stable phases -> anneal"
    assert normalization["overrode_edge_relations"] == 1


def test_normalize_raw_graph_payload_ignores_agent_relation_for_workflow_edges():
    eid = uuid.uuid4()
    payload, normalization = normalize_raw_graph_payload(
        {
            "nodes": [
                {"raw_name": "powder", "node_kind": "object", "evidence_text": "powder"},
                {"raw_name": "anneal", "node_kind": "operation", "evidence_text": "anneal"},
                {"raw_name": "sample", "node_kind": "object", "evidence_text": "sample"},
            ],
            "edges": [
                {
                    "source": "powder",
                    "target": "anneal",
                    "relation_type": "same_as",
                    "evidence_text": "powder was annealed",
                },
                {
                    "source": "anneal",
                    "target": "sample",
                    "relation_type": "input_to",
                    "evidence_text": "annealing produced sample",
                },
            ],
        },
        _row(uuid.uuid4()),
        eid,
    )

    graph = RawWorkflowGraph.model_validate(payload)

    assert [edge.relation_type for edge in graph.edges] == ["input_to", "produces"]
    assert normalization["overrode_edge_relations"] == 2
