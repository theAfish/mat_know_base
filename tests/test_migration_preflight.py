import json

import pytest

from mkb import KnowledgeBase, ValidationError
from mkb.migration_preflight import compare_migration_inventories, load_inventory


def _inventory():
    return {
        "format_version": 1,
        "database": {
            "tables": {
                "research_projects": {
                    "row_count": 2,
                    "primary_key_columns": ["project_id"],
                    "primary_keys": ["project-a", "project-b"],
                },
                "project_assets": {
                    "row_count": 1,
                    "primary_key_columns": ["project_id", "asset_id"],
                    "primary_keys": [["project-a", "asset-a"]],
                },
            }
        },
        "object_storage": {
            "buckets": {
                "raw": {
                    "object_count": 1,
                    "total_bytes": 4,
                    "objects": [
                        {"key": "paper.pdf", "bytes": 4, "etag": "etag-a"}
                    ],
                }
            }
        },
        "local_files": {
            "file_count": 1,
            "total_bytes": 4,
            "files": [
                {
                    "root": "data/processed",
                    "path": "paper.md",
                    "bytes": 4,
                    "sha256": "sha-a",
                }
            ],
        },
    }


def test_preflight_allows_additive_rows_objects_and_files():
    before = _inventory()
    after = json.loads(json.dumps(before))
    table = after["database"]["tables"]["research_projects"]
    table["row_count"] = 3
    table["primary_keys"].append("project-c")
    bucket = after["object_storage"]["buckets"]["raw"]
    bucket["objects"].append({"key": "new.pdf", "bytes": 2, "etag": "etag-new"})
    bucket["object_count"] = 2
    files = after["local_files"]["files"]
    files.append(
        {
            "root": "data/processed",
            "path": "new.md",
            "bytes": 2,
            "sha256": "sha-new",
        }
    )

    result = compare_migration_inventories(before, after)

    assert result["ok"] is True
    assert result["blockers"] == []
    assert result["database"]["tables"]["research_projects"][
        "added_primary_keys"
    ] == ["project-c"]
    assert result["object_storage"]["buckets"]["raw"]["added_keys"] == [
        "new.pdf"
    ]


def test_preflight_blocks_missing_ids_and_changed_content():
    before = _inventory()
    after = json.loads(json.dumps(before))
    projects = after["database"]["tables"]["research_projects"]
    projects["row_count"] = 1
    projects["primary_keys"].remove("project-b")
    stored = after["object_storage"]["buckets"]["raw"]["objects"][0]
    stored.update(bytes=5, etag="etag-changed")
    local = after["local_files"]["files"][0]
    local.update(bytes=5, sha256="sha-changed")

    result = compare_migration_inventories(before, after)

    assert result["ok"] is False
    assert [item["category"] for item in result["blockers"]] == [
        "missing_database_id",
        "changed_object",
        "changed_local_file",
    ]
    assert result["summary"]["blocker_count"] == 3


def test_preflight_service_and_inventory_loader_are_read_only(tmp_path):
    source = tmp_path / "inventory.json"
    source.write_text(json.dumps(_inventory()), encoding="utf-8")
    original = source.read_bytes()

    assert load_inventory(source)["format_version"] == 1
    with KnowledgeBase() as kb:
        report = kb.maintenance.compare_inventories(source, source)

    assert report.ok is True
    assert source.read_bytes() == original

    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError, match="Unsupported"):
        load_inventory(invalid)
