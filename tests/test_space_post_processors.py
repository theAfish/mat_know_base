from types import SimpleNamespace
import uuid
from pathlib import Path

import pytest

from mkb.spaces.registry import _normalize_post_processors, resolve_post_processor


class _ScriptSession:
    def __init__(self, path, filename="gate.py"):
        self.script = SimpleNamespace(
            script_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name=Path(filename).stem,
            filename=filename,
            storage_path=str(path / filename),
            created_at=None,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def query(self, *_args):
        return self

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return self.script


def _script_session(path, filename="gate.py"):
    return _ScriptSession(path, filename)


def test_normalize_post_processors_builds_default_from_legacy_settings():
    processors = _normalize_post_processors(
        None,
        legacy_defaults={
            "review_prompt": "legacy prompt",
            "review_allow_search": True,
            "review_search_tools": ["uniprot"],
        },
    )

    assert processors == [
        {
            "id": "default",
            "name": "Default reviewer",
            "description": "General projection review and correction.",
            "prompt": "legacy prompt",
            "tool_groups": ["reading", "uniprot"],
            "skill_ids": [],
            "output_columns": [],
            "enabled": True,
        }
    ]


def test_resolve_post_processor_selects_named_profile_without_forcing_reading():
    space = SimpleNamespace(
        review_prompt=None,
        review_allow_search=False,
        review_search_tools=["web"],
        post_processors=[
            {
                "id": "schema_only",
                "name": "Schema only",
                "prompt": "schema prompt",
                "tool_groups": [],
            },
            {
                "id": "sequence_enricher",
                "name": "Sequence enricher",
                "prompt": "sequence prompt",
                "tool_groups": ["uniprot"],
            },
        ],
    )

    selected = resolve_post_processor(space, "sequence_enricher")

    assert selected["id"] == "sequence_enricher"
    assert selected["prompt"] == "sequence prompt"
    assert selected["tool_groups"] == ["uniprot"]


def test_normalize_post_processors_preserves_attached_skill_ids():
    processors = _normalize_post_processors(
        [
            {
                "id": "skilled",
                "name": "Skilled reviewer",
                "tool_groups": ["reading"],
                "skill_ids": ["abc", "abc", "def"],
                "output_columns": ["canonical_name", {"name": "sequence_normalized", "description": "FASTA sequence"}],
            }
        ]
    )

    assert processors[0]["skill_ids"] == ["abc", "def"]
    assert processors[0]["output_columns"] == [
        {"name": "canonical_name", "description": ""},
        {"name": "sequence_normalized", "description": "FASTA sequence"},
    ]


def test_projection_reviewer_output_column_prompt_allows_only_declared_columns():
    from mkb.agents.projection_reviewer import _post_processor_output_column_block

    block = _post_processor_output_column_block([
        {"name": "canonical_name", "description": "Normalized display name"},
        {"name": "sequence_fasta", "description": ""},
    ])

    assert "`canonical_name`: Normalized display name" in block
    assert "`sequence_fasta`" in block
    assert "Do not invent alternate names" in block


def test_projection_reviewer_output_column_prompt_blocks_new_columns_by_default():
    from mkb.agents.projection_reviewer import _post_processor_output_column_block

    block = _post_processor_output_column_block([])

    assert "no user-declared output columns" in block
    assert "Do not add new columns" in block


def test_post_processor_script_returns_agent_decision(monkeypatch, tmp_path):
    from mkb.post_processors.registry import run_script

    script = tmp_path / "gate.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'run_agent': payload['project']['needs_review'], 'context': 'checked'}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mkb.post_processors.registry.SyncSessionLocal",
        lambda: _script_session(tmp_path),
    )

    decision = run_script(
        {"script_id": "11111111-1111-1111-1111-111111111111", "timeout_seconds": 5},
        {"project": {"needs_review": False}},
    )

    assert decision == {
        "run_agent": False,
        "context": "checked",
        "patch": None,
        "script": {"script_id": "11111111-1111-1111-1111-111111111111", "name": "gate", "filename": "gate.py", "created_at": None, "timeout_seconds": 5},
    }


def test_post_processor_script_requires_explicit_agent_decision(monkeypatch, tmp_path):
    from mkb.post_processors.registry import run_script

    (tmp_path / "invalid.py").write_text("print('{}')\n", encoding="utf-8")
    monkeypatch.setattr(
        "mkb.post_processors.registry.SyncSessionLocal",
        lambda: _script_session(tmp_path, filename="invalid.py"),
    )

    with pytest.raises(ValueError, match="boolean 'run_agent'"):
        run_script(
            {"script_id": "11111111-1111-1111-1111-111111111111"},
            {"project": {}},
        )


def test_post_processor_script_preserves_valid_database_patch(monkeypatch, tmp_path):
    from mkb.post_processors.registry import run_script

    (tmp_path / "patch.py").write_text(
        "import json\n"
        "print(json.dumps({'run_agent': False, 'patch': {"
        "'winning_projection_id': 'winner', 'updates': [{'path': 'templates[0].normalized_sequence', 'value': 'MPEPTIDE'}]}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "mkb.post_processors.registry.SyncSessionLocal",
        lambda: _script_session(tmp_path, filename="patch.py"),
    )

    decision = run_script(
        {"script_id": "11111111-1111-1111-1111-111111111111"},
        {"project": {}},
    )

    assert decision["run_agent"] is False
    assert decision["patch"] == {
        "winning_projection_id": "winner",
        "updates": [{"path": "templates[0].normalized_sequence", "value": "MPEPTIDE"}],
    }
