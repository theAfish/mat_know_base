import ast
from pathlib import Path


def test_fastapi_routes_do_not_import_persistence_or_storage_internals():
    root = Path(__file__).resolve().parents[1] / "src/mkb/web/routers"
    forbidden = (
        "mkb.config",
        "mkb.db",
        "mkb.post_processors",
        "mkb.skills",
        "mkb.storage",
        "mkb.web._state",
    )
    violations = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(forbidden):
                    violations.append(f"{path.name}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        violations.append(f"{path.name}:{node.lineno}:{alias.name}")
    assert violations == []
