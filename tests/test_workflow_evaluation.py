import json
from pathlib import Path

from mkb.workflows.evaluation import evaluate_raw_workflow


def test_gold_workflow_scores_perfectly_and_detects_hallucination():
    gold = json.loads((Path(__file__).parent / "fixtures/workflows/annealing_gold.json").read_text())
    result = evaluate_raw_workflow(gold, gold)
    assert result["node_precision"] == result["edge_recall"] == 1.0
    assert not result["evidence_mismatches"]

    predicted = json.loads(json.dumps(gold))
    predicted["nodes"][0]["raw_name"] = "inferred starting material"
    result = evaluate_raw_workflow(predicted, gold)
    assert result["hallucinated_nodes"]
    assert result["missing_nodes"]
