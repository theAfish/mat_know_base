import pytest
import sys
from types import SimpleNamespace

sys.modules.setdefault(
    "mkb.agents.extraction",
    SimpleNamespace(
        build_extraction_agent=lambda *args, **kwargs: None,
        run_extraction=lambda *args, **kwargs: None,
        run_extraction_all=lambda *args, **kwargs: None,
    ),
)
sys.modules.setdefault(
    "mkb.agents.runner",
    SimpleNamespace(AgentRunner=object, RunResult=object),
)

from mkb.agents.tools.projection_review import _set_patch_value, _summarize_data_changes  # noqa: E402


def test_set_patch_value_updates_nested_list_field():
    data = {
        "templates": [
            {
                "name": "template A",
                "sequence": "",
                "metadata": {"source": "old"},
            }
        ]
    }

    _set_patch_value(data, "templates[0].sequence", "MPEPTIDE")
    _set_patch_value(data, "templates[0].metadata.source", "verified")

    assert data["templates"][0]["sequence"] == "MPEPTIDE"
    assert data["templates"][0]["metadata"]["source"] == "verified"


def test_set_patch_value_rejects_missing_list_index():
    data = {"templates": []}

    with pytest.raises(ValueError, match="out of range"):
        _set_patch_value(data, "templates[0].sequence", "MPEPTIDE")


def test_summarize_data_changes_marks_noop_explicitly():
    summary = _summarize_data_changes({"templates": [{"sequence": ""}]}, {"templates": [{"sequence": ""}]})

    assert summary["changed_count"] == 0
    assert summary["data_changed"] is False
    assert summary["sequence_filled"] is False


def test_summarize_data_changes_marks_sequence_fill_explicitly():
    summary = _summarize_data_changes({"templates": [{"sequence": ""}]}, {"templates": [{"sequence": "MPEPTIDE"}]})

    assert summary["data_changed"] is True
    assert summary["sequence_filled"] is True
    assert summary["sequence_filled_count"] == 1
