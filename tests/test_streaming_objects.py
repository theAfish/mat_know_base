import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from mkb import KnowledgeBase
from mkb.adapters import SQLAlchemyDatabase, SqlObjectStore
from mkb.adapters.object_store import object_blobs_table, object_chunks_table
from tests.test_adapter_conformance import _object_store_for


class GeneratedFile:
    def __init__(self, size, *, fail_after=None):
        self.size, self.position, self.fail_after = size, 0, fail_after

    def read(self, size):
        assert 0 < size <= 256 * 1024, "Input was requested as a whole-file buffer"
        if self.fail_after is not None and self.position >= self.fail_after:
            raise OSError("source interrupted")
        amount = min(size, self.size - self.position)
        self.position += amount
        return b"x" * amount


@pytest.mark.parametrize("kind", ["filesystem", "sql", "s3"])
def test_stream_writes_return_verified_metadata_and_bounded_content(kind, tmp_path):
    store = _object_store_for(kind, tmp_path)
    size = 7 * 1024 * 1024 + 17  # Includes a non-final multipart part on S3.
    info = store.put_stream("raw", "large.bin", GeneratedFile(size))
    digest = hashlib.sha256()
    with store.open("raw", "large.bin") as stream:
        total = 0
        while chunk := stream.read(256 * 1024):
            total += len(chunk)
            digest.update(chunk)
    assert total == info.size == size
    assert info.sha256 == digest.hexdigest()


@pytest.mark.parametrize("kind", ["filesystem", "sql", "s3"])
@pytest.mark.parametrize("failure", ["source", "limit", "checksum"])
def test_failed_stream_does_not_replace_existing_object_or_leave_staging(kind, failure, tmp_path):
    store = _object_store_for(kind, tmp_path)
    store.put_bytes("raw", "same.bin", b"previous")
    source = GeneratedFile(8 * 1024 * 1024,
        fail_after=6 * 1024 * 1024 if failure == "source" else None)
    with pytest.raises((OSError, ValueError), match="interrupted|limit|checksum"):
        store.put_stream("raw", "same.bin", source,
            max_bytes=6 * 1024 * 1024 if failure == "limit" else None,
            expected_sha256='0' * 64 if failure == "checksum" else None)
    assert store.get_bytes("raw", "same.bin") == b"previous"
    assert [(item.key, item.size) for item in store.list("raw")] == [("same.bin", 8)]
    if kind == "filesystem":
        assert not list(tmp_path.rglob(".mkb-upload-*"))
    elif kind == "s3":
        assert not store._client.uploads


def test_sql_stream_reads_legacy_blobs_and_new_chunks_with_seek(tmp_path):
    database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'objects.db'}")
    store = SqlObjectStore(database)
    data = bytes(range(256)) * 2049
    with database.transaction() as session:
        session.execute(insert(object_blobs_table).values(bucket="raw", key="legacy.bin", data=data,
            size=len(data), etag=hashlib.sha256(data).hexdigest(), last_modified=datetime.now(timezone.utc)))
    store.put_stream("raw", "chunked.bin", io.BytesIO(data))
    with database.session() as session:
        assert session.execute(select(object_blobs_table.c.data)
            .where(object_blobs_table.c.key == "chunked.bin")).scalar_one() == b""
        assert max(len(row[0]) for row in session.execute(select(object_chunks_table.c.data))) <= 256 * 1024
    for key in ("legacy.bin", "chunked.bin"):
        with store.open("raw", key) as stream:
            assert stream.read(17) == data[:17]
            assert stream.seek(256 * 1024 - 3) == 256 * 1024 - 3
            assert stream.read(13) == data[256 * 1024 - 3:256 * 1024 + 10]
            stream.seek(-11, io.SEEK_END)
            assert stream.read() == data[-11:]
        store.delete("raw", key)
    with database.session() as session:
        assert not session.execute(select(object_chunks_table)).first()


def test_sql_open_never_mixes_replaced_object_generations(tmp_path):
    store = _object_store_for("sql", tmp_path)
    store.put_stream("raw", "same.bin", GeneratedFile(512 * 1024))
    with store.open("raw", "same.bin") as stream:
        assert stream.read(256 * 1024) == b"x" * (256 * 1024)
        store.put_stream("raw", "same.bin", io.BytesIO(b"y" * (512 * 1024)))
        with pytest.raises(OSError, match="changed|truncated"):
            stream.read(256 * 1024)


def test_source_add_file_and_artifact_stream_use_canonical_io_without_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / "large.bin"
    with path.open("wb") as stream:
        for _ in range(5):
            stream.write(b"x" * (256 * 1024))
    def forbidden(*args, **kwargs):
        pytest.fail("A path import eagerly read the whole input")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'knowledge.db'}", object_store_url="sql:") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Streaming")
        source = kb.sources.add_file(collection.id, path)
        with kb.sources.open(source.id) as content:
            artifact = kb.artifacts.add_stream(source.id, content, processing_type="COPY", format="bin")
        assert source.size == artifact.size == path.stat().st_size
        assert source.sha256 == artifact.sha256
        assert kb.sources.get(source.id).storage.key == f"sources/{source.id}"


@pytest.mark.parametrize('resource', ['sources', 'artifacts'])
@pytest.mark.parametrize('mismatch', ['size', 'checksum'])
def test_stream_registration_verifies_before_publish_and_allows_same_id_retry(tmp_path, resource, mismatch):
    with KnowledgeBase.from_url(database_url=f"sqlite:///{tmp_path / 'knowledge.db'}", object_store_url="sql:") as kb:
        kb.initialize()
        collection = kb.collections.create(name="Verified import")
        identifier = uuid4()
        content = b'original retained file'
        expected_size = len(content)
        expected_sha256 = hashlib.sha256(content).hexdigest()
        service = getattr(kb, resource)
        if resource == 'sources':
            parent = collection.id
            arguments = {'filename': 'input.bin', 'source_id': identifier}
        else:
            parent = kb.sources.add_bytes(collection.id, b'source', filename='source.bin').id
            arguments = {'processing_type': 'COPY', 'format': 'bin', 'artifact_id': identifier}
        with pytest.raises(ValueError, match='size|checksum'):
            service.add_stream(parent, io.BytesIO(content), **arguments,
                expected_size=expected_size + 1 if mismatch == 'size' else expected_size,
                expected_sha256='0' * 64 if mismatch == 'checksum' else expected_sha256)
        assert service.get(identifier) is None
        assert not kb.object_store.exists(service._default_bucket, f'{resource}/{identifier}')
        stored = service.add_stream(parent, io.BytesIO(content), **arguments,
            expected_size=expected_size, expected_sha256=expected_sha256)
        assert stored.id == identifier and stored.size == expected_size and stored.sha256 == expected_sha256
        with service.open(identifier) as stream:
            assert stream.read() == content
