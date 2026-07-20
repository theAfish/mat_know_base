import os
from pathlib import Path

import pytest

from mkb.maintenance import apply_retention, retention_plan


def test_retention_is_dry_run_and_never_targets_root(tmp_path: Path):
    root = tmp_path / "uploads"
    child = root / "abandoned"
    child.mkdir(parents=True)
    (child / "partial.bin").write_bytes(b"abc")
    os.utime(child, (1, 1))
    plan = retention_plan(older_than_days=1, roots=[root])
    assert plan["dry_run"] is True
    assert plan["items"] == [{"kind": "local", "path": str(child), "bytes": 3}]
    assert child.exists()


def test_retention_requires_typed_confirmation(tmp_path: Path):
    target = tmp_path / "old"
    target.write_text("x")
    plan = {"items": [{"path": str(target), "bytes": 1}]}
    with pytest.raises(ValueError, match="DELETE"):
        apply_retention(plan, confirm="yes")
    assert target.exists()
    assert apply_retention(plan, confirm="DELETE")["removed"] == 1
