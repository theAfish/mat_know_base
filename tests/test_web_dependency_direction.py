import ast
from pathlib import Path


def test_fastapi_routes_do_not_import_persistence_or_storage_internals():
    root = Path(__file__).resolve().parents[1] / "src/mkb/web/routers"
    forbidden = (
        "mkb.api",
        "mkb.config",
        "mkb.db",
        "mkb.post_processors",
        "mkb.services",
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


def test_fastapi_entrypoint_does_not_import_legacy_facade_or_domain_services():
    path = Path(__file__).resolve().parents[1] / "src/mkb/web/api_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = ("mkb.api", "mkb.services")
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(forbidden):
                violations.append(f"{node.lineno}:{node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(forbidden):
                    violations.append(f"{node.lineno}:{alias.name}")
    assert violations == []
