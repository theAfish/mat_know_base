from pathlib import Path
from types import SimpleNamespace

from mkb.api import (
    _choose_asset_for_manual_output,
    _inspect_manual_processed_dir,
    _matches_search_tokens,
    _normalize_search_query,
)
from mkb.db.models import ProcessingType


def test_inspect_manual_processed_dir_prefers_markdown_and_tracks_artifacts(tmp_path: Path):
    processed_dir = tmp_path / "processed"
    (processed_dir / "images").mkdir(parents=True)
    (processed_dir / "paper.md").write_text("# Hello\n", encoding="utf-8")
    (processed_dir / "images" / "fig1.png").write_bytes(b"png-bytes")

    info = _inspect_manual_processed_dir(processed_dir)

    assert info["primary_relpath"] == "paper.md"
    assert info["output_format"] == "md"
    assert info["processing_type"] == ProcessingType.MARKDOWN
    assert info["artifact_files"] == ["images/fig1.png"]
    assert info["size_bytes"] == len(b"# Hello\n")


def test_choose_asset_for_manual_output_matches_same_stem():
    asset = SimpleNamespace(asset_id="asset-1", filename="1-s2.0-S8756328214003457-main.pdf")
    other = SimpleNamespace(asset_id="asset-2", filename="supplement.csv")

    chosen = _choose_asset_for_manual_output(
        [other, asset],
        primary_name="1-s2.0-S8756328214003457-main.md",
    )

    assert chosen.asset_id == "asset-1"


def test_normalize_search_query_discards_empty_tokens():
    assert _normalize_search_query("  enamel   mineralization  paper  ") == [
        "enamel",
        "mineralization",
        "paper",
    ]


def test_matches_search_tokens_requires_all_tokens_across_values():
    assert _matches_search_tokens(
        "enamel paper.pdf",
        "application/json",
        "hydroxyapatite mineralization dataset",
        tokens=["enamel", "mineralization"],
    )
    assert not _matches_search_tokens(
        "enamel paper.pdf",
        "application/json",
        tokens=["enamel", "csv"],
    )
