import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.snapshot_manifest import (
    enrich_inventory_with_manifest,
    safe_extract,
    write_manifest,
)


def test_manifest_round_trip(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "dump.sql").write_text("select 1;")
    write_manifest(source, "head", "test")
    archive = tmp_path / "snapshot.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=".")
    destination = tmp_path / "staging"
    safe_extract(archive, destination)
    assert json.loads((destination / "manifest.json").read_text())["schema_revision"] == "head"


def test_snapshot_rejects_traversal(tmp_path: Path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        bundle.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="unsafe"):
        safe_extract(archive, tmp_path / "staging")


def test_manifest_enriches_historical_inventory_with_content_hashes():
    inventory = {
        "object_storage": {
            "buckets": {
                "raw": {
                    "object_count": 1,
                    "total_bytes": 3,
                    "objects": [{"key": "old", "bytes": 3, "etag": "legacy"}],
                }
            }
        }
    }
    manifest = {
        "files": {
            "minio/raw/old": {"bytes": 3, "sha256": "old-sha"},
            "minio/raw/recovered": {"bytes": 4, "sha256": "new-sha"},
            "local/data/file": {"bytes": 1, "sha256": "ignored"},
        }
    }

    result = enrich_inventory_with_manifest(inventory, manifest)
    bucket = result["object_storage"]["buckets"]["raw"]

    assert bucket["object_count"] == 2
    assert bucket["total_bytes"] == 7
    assert bucket["objects"] == [
        {"key": "old", "bytes": 3, "etag": "legacy", "sha256": "old-sha"},
        {"key": "recovered", "bytes": 4, "sha256": "new-sha"},
    ]
    assert "sha256" not in inventory["object_storage"]["buckets"]["raw"]["objects"][0]
