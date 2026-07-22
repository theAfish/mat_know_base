import ast
from pathlib import Path

from mkb.jobs import DatabaseJobStore
import pytest


def test_no_source_imports_the_removed_legacy_context():
    root = Path(__file__).resolve().parents[1] / "src/mkb"
    forbidden = "mkb.legacy_context"
    violations = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == forbidden or node.module.startswith(f"{forbidden}."):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                        violations.append(
                            f"{path.relative_to(root)}:{node.lineno}:{alias.name}"
                        )
    assert violations == []


def test_database_job_store_requires_an_explicit_database_resource():
    with pytest.raises(TypeError):
        DatabaseJobStore()


def test_migrated_agent_entrypoints_do_not_import_global_storage_helpers():
    root = Path(__file__).resolve().parents[1] / "src/mkb"
    migrated = [
        "agents/extraction.py",
        "agents/review.py",
        "agents/clarification.py",
        "agents/knowledge_graph.py",
        "agents/graph_review.py",
        "agents/projection.py",
        "agents/projection_reviewer.py",
        "agents/workflow_extraction.py",
        "agents/workflow_canonicalization.py",
        "agents/tools/reading.py",
        "agents/tools/frames.py",
        "agents/tools/projection.py",
        "agents/tools/knowledge_graph.py",
        "agents/tools/graph_review.py",
        "agents/tools/projection_review.py",
        "agents/tools/workflows.py",
        "agents/tools/workflow_canonicalization.py",
    ]
    forbidden = {"mkb.db.engine", "mkb.storage.s3"}
    violations = []
    for relative_path in migrated:
        path = root / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                violations.append(f"{relative_path}:{node.lineno}:{node.module}")
    assert violations == []
