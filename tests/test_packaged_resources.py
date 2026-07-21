from pathlib import Path

from mkb.db.engine import alembic_config
from mkb.resources import alembic_paths


def test_alembic_resources_resolve_without_current_working_directory(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config_path, script_path = alembic_paths()
    assert config_path.name == "alembic.ini"
    assert (script_path / "env.py").is_file()
    assert list((script_path / "versions").glob("*.py"))
    assert alembic_config().get_main_option("script_location") == str(script_path)
