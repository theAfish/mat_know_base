import ast
import json
from pathlib import Path
from types import SimpleNamespace

from mkb import cli


class _Client:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.materials = SimpleNamespace(
            library=SimpleNamespace(search=self._search)
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def ingest(self, directory, *, label=None):
        self.calls.append(("ingest", directory, label))
        return {"status": "ok", "directory": directory}

    def service(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return {
                "projects": [],
                "assets": [],
                "total": 0,
            }

        return call

    def _search(self, **kwargs):
        self.calls.append(("materials.library.search", kwargs))
        return {"projects": [], "assets": [], "total": 0}


def test_cli_does_not_import_legacy_api_directly():
    path = Path(cli.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]

    forbidden = {
        "mkb.api",
        "mkb.config",
        "mkb.maintenance",
        "mkb.migration_inventory",
        "mkb.runtime_settings",
        "mkb.spaces.registry",
    }
    assert forbidden.isdisjoint(imports)


def test_core_cli_command_uses_explicit_knowledge_base(monkeypatch, capsys):
    client = _Client()
    monkeypatch.setattr(cli, "_knowledge_base", lambda: client)

    cli.cmd_ingest(SimpleNamespace(directory="papers", label="Test"))

    assert client.calls == [("ingest", "papers", "Test")]
    assert client.closed is True
    assert json.loads(capsys.readouterr().out) == {
        "status": "ok",
        "directory": "papers",
    }


def test_materials_cli_command_uses_grouped_service(monkeypatch, capsys):
    client = _Client()
    monkeypatch.setattr(cli, "_knowledge_base", lambda: client)

    cli.cmd_search(
        SimpleNamespace(query="alloy", limit=5, project_id=None)
    )

    assert client.calls == [
        (
            "materials.library.search",
            {"query": "alloy", "limit": 5, "project_id": None},
        )
    ]
    assert client.closed is True
    assert capsys.readouterr().out == "No matches found.\n"
