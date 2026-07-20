import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts.snapshot_manifest import safe_extract, write_manifest


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
