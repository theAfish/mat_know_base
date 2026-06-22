"""Deterministic Phase-1 checks against manually reviewed raw workflows."""

from __future__ import annotations

from mkb.workflows.contract import RawWorkflowGraph

GRANULARITY_RELATIONS = {"same_as", "part_of", "has_part", "expands_to", "summarized_by"}


def evaluate_raw_workflow(predicted: dict, gold: dict) -> dict:
    """Compare raw-name nodes and directed relation triples with a gold graph.

    Raw names are intentionally compared exactly (apart from surrounding
    whitespace): evaluation must not introduce synonym normalization.
    """
    pred = RawWorkflowGraph.model_validate(predicted)
    truth = RawWorkflowGraph.model_validate(gold)

    def node_key(node):
        return (node.raw_name.strip(), node.node_kind_guess)

    pred_nodes = {node_key(node): node for node in pred.nodes}
    gold_nodes = {node_key(node): node for node in truth.nodes}
    pred_by_id = {node.node_id: node_key(node) for node in pred.nodes}
    gold_by_id = {node.node_id: node_key(node) for node in truth.nodes}

    def edge_key(edge, by_id):
        return (by_id[edge.source_node], edge.relation_type, by_id[edge.target_node])

    pred_edges = {edge_key(edge, pred_by_id): edge for edge in pred.edges}
    gold_edges = {edge_key(edge, gold_by_id): edge for edge in truth.edges}
    shared_nodes = pred_nodes.keys() & gold_nodes.keys()
    shared_edges = pred_edges.keys() & gold_edges.keys()

    evidence_mismatches = []
    for key in shared_nodes:
        expected = gold_nodes[key].evidence_text.strip()
        actual = pred_nodes[key].evidence_text.strip()
        if expected and expected not in actual and actual not in expected:
            evidence_mismatches.append({"element": "node", "key": key, "expected": expected, "actual": actual})
    for key in shared_edges:
        expected = gold_edges[key].evidence_text.strip()
        actual = pred_edges[key].evidence_text.strip()
        if expected and expected not in actual and actual not in expected:
            evidence_mismatches.append({"element": "edge", "key": key, "expected": expected, "actual": actual})

    pred_granularity = {key for key in pred_edges if key[1] in GRANULARITY_RELATIONS}
    gold_granularity = {key for key in gold_edges if key[1] in GRANULARITY_RELATIONS}
    return {
        "missing_nodes": sorted(gold_nodes.keys() - pred_nodes.keys()),
        "hallucinated_nodes": sorted(pred_nodes.keys() - gold_nodes.keys()),
        "incorrect_edges": sorted(pred_edges.keys() - gold_edges.keys()),
        "missing_edges": sorted(gold_edges.keys() - pred_edges.keys()),
        "evidence_mismatches": evidence_mismatches,
        "granularity_inconsistencies": sorted(pred_granularity ^ gold_granularity),
        "node_precision": len(shared_nodes) / len(pred_nodes) if pred_nodes else (1.0 if not gold_nodes else 0.0),
        "node_recall": len(shared_nodes) / len(gold_nodes) if gold_nodes else 1.0,
        "edge_precision": len(shared_edges) / len(pred_edges) if pred_edges else (1.0 if not gold_edges else 0.0),
        "edge_recall": len(shared_edges) / len(gold_edges) if gold_edges else 1.0,
    }
