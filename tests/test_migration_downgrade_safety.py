import importlib.util
from pathlib import Path

import pytest


def _migration_module():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0023_durable_jobs.py"
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, count):
        self.count = count

    def scalar_one(self):
        return self.count


class _Bind:
    def __init__(self, count):
        self.count = count

    def execute(self, statement):
        assert str(statement) == "SELECT count(*) FROM background_jobs"
        return _Result(self.count)


def test_durable_jobs_downgrade_refuses_to_drop_populated_history(monkeypatch):
    migration = _migration_module()
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Bind(5))
    dropped = []
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    with pytest.raises(RuntimeError, match="Refusing to drop populated"):
        migration.downgrade()

    assert dropped == []


def test_durable_jobs_downgrade_allows_empty_new_table(monkeypatch):
    migration = _migration_module()
    monkeypatch.setattr(migration.op, "get_bind", lambda: _Bind(0))
    dropped = []
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    migration.downgrade()

    assert dropped == ["background_jobs"]
