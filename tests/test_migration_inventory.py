from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from mkb.migration_inventory import local_inventory, object_storage_inventory


class _Paginator:
    def paginate(self, *, Bucket):
        assert Bucket == "raw"
        return [{
            "Contents": [
                {
                    "Key": "z/document.pdf",
                    "Size": 3,
                    "ETag": '"abc"',
                    "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc),
                },
                {"Key": "a/notes.md", "Size": 5, "ETag": '"def"'},
            ]
        }]


class _S3Client:
    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Paginator()

    def get_object(self, *, Bucket, Key):
        assert Bucket == "raw"
        return {"Body": BytesIO({"a/notes.md": b"notes", "z/document.pdf": b"pdf"}[Key])}


def test_local_inventory_is_deterministic_and_checksummed(tmp_path: Path):
    root = tmp_path / "papers"
    root.mkdir()
    (root / "b.txt").write_bytes(b"b")
    (root / "a.txt").write_bytes(b"a")

    result = local_inventory([root])

    assert result["file_count"] == 2
    assert result["total_bytes"] == 2
    assert [item["path"] for item in result["files"]] == ["a.txt", "b.txt"]
    assert result["files"][0]["sha256"] == (
        "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    )


def test_object_inventory_sorts_keys_and_preserves_identity_metadata():
    result = object_storage_inventory(_S3Client(), ["raw"])

    bucket = result["buckets"]["raw"]
    assert bucket["object_count"] == 2
    assert bucket["total_bytes"] == 8
    assert [item["key"] for item in bucket["objects"]] == ["a/notes.md", "z/document.pdf"]
    assert bucket["objects"][1]["etag"] == "abc"
    assert bucket["objects"][1]["last_modified"] == "2026-01-02T00:00:00+00:00"


def test_object_inventory_can_include_streamed_content_checksums():
    result = object_storage_inventory(_S3Client(), ["raw"], include_checksums=True)

    assert result["buckets"]["raw"]["objects"][0]["sha256"] == (
        "ab5aa97074c454a0632057e704220d9a6678fbf773a0a5806fc09b8173b07309"
    )
