import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import snapshot_payload as payload


def settings(**overrides):
    return SimpleNamespace(**({
        'object_store_backend': 'sql',
        'object_store_root': 'data/objects',
        'processed_local_root': 'data/processed',
        'runtime_settings_path': 'data/runtime_settings.json',
        's3_bucket_raw': 'raw',
        's3_bucket_processed': 'processed',
        's3_bucket_archive': 'archive',
        's3_bucket_temp': 'temp',
        'pg_dsn_sync': 'postgresql+psycopg://test:test@localhost/test',
    } | overrides))


def test_business_files_round_trip_without_settings(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    files = {
        'data/skills/example/SKILL.md': '# Example skill',
        'data/skills/example/scripts/helper.py': 'print(1)',
        'data/post_processor_scripts/process.py': 'print(2)',
        'data/exports/result.json': '{}',
        'data/processed/job/manifest.json': '{"asset": 1}',
        'data/runtime_settings.json': 'DO-NOT-INCLUDE',
        'data/custom-settings.json': 'DO-NOT-INCLUDE-EITHER',
        '.env': 'API_KEY=DO-NOT-INCLUDE',
        'config.yaml': 'DO-NOT-INCLUDE',
    }
    for name, value in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    (source / 'data/inbox').mkdir()
    staging = tmp_path / 'staging'
    config = settings(runtime_settings_path='data/custom-settings.json')
    payload.stage_local(config, source, staging)
    actual = {str(p.relative_to(staging / 'local')) for p in (staging / 'local').rglob('*') if p.is_file()}
    assert actual == {name for name in files if name.startswith('data/') and 'settings' not in name}
    assert json.loads((staging / 'snapshot.json').read_text())['settings_included'] is False
    target = tmp_path / 'restored'
    (target / 'data').mkdir(parents=True)
    (target / 'data/runtime_settings.json').write_text('preserve-target-settings')
    payload.restore_local(config, staging / 'local', target)
    for name in actual:
        assert (target / name).read_bytes() == (source / name).read_bytes()
    assert (target / 'data/inbox').is_dir()
    assert (target / 'data/runtime_settings.json').read_text() == 'preserve-target-settings'


def test_file_backend_and_custom_processed_root_are_included(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name in ['objects/raw/file.pdf', 'outputs/result.txt']:
        path = source / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b'payload')
    config = settings(object_store_backend='file', object_store_root='objects', processed_local_root='outputs')
    payload.stage_local(config, source, tmp_path / 'staging')
    assert (tmp_path / 'staging/local/objects/raw/file.pdf').read_bytes() == b'payload'
    assert (tmp_path / 'staging/local/outputs/result.txt').is_file()


def test_external_data_root_fails_instead_of_silently_omitting_files(tmp_path):
    with pytest.raises(ValueError):
        payload.local_paths(settings(processed_local_root='/external/data'), tmp_path)


def test_restore_checks_all_paths_before_writing(tmp_path):
    source = tmp_path / 'source'
    (source / 'data').mkdir(parents=True)
    (source / 'data/file').write_text('new')
    (source / '.env').write_text('must not be restored')
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(ValueError, match='Unexpected'):
        payload.restore_local(settings(), source, target)
    assert not (target / 'data/file').exists()


def test_restore_rejects_existing_symlink(tmp_path):
    source = tmp_path / 'source'
    (source / 'data/skills').mkdir(parents=True)
    (source / 'data/skills/SKILL.md').write_text('skill')
    target = tmp_path / 'target'
    (target / 'data').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (target / 'data/skills').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        payload.restore_local(settings(), source, target)
    assert not list(outside.iterdir())


@pytest.mark.parametrize('key', ['../escape', '/absolute', 'a/../b', 'a//b', './a', 'a\\b'])
def test_invalid_object_keys_fail_without_normalizing(tmp_path, key):
    with pytest.raises(ValueError):
        payload.object_path(tmp_path, 'raw', key)


def test_s3_round_trip_preserves_all_objects_and_removes_stale_keys(tmp_path, monkeypatch):
    class Client:
        def __init__(self):
            self.objects = {'raw': {'folder/a': b'one', 'b': b'two'}, 'processed': {}, 'archive': {}, 'temp': {}}

        def get_paginator(self, _name):
            return self

        def paginate(self, *, Bucket):
            return [{'Contents': [{'Key': key, 'Size': len(value), 'ETag': key} for key, value in self.objects[Bucket].items()]}]

        def get_object(self, *, Bucket, Key, IfMatch):
            assert IfMatch == Key
            return {'Body': io.BytesIO(self.objects[Bucket][Key])}

        def head_bucket(self, *, Bucket):
            assert Bucket in self.objects

        def upload_file(self, filename, bucket, key):
            self.objects[bucket][key] = Path(filename).read_bytes()

        def delete_object(self, *, Bucket, Key):
            del self.objects[Bucket][Key]

    client = Client()
    original = {name: dict(objects) for name, objects in client.objects.items()}
    monkeypatch.setattr(payload, 's3_client', lambda _: client)
    payload.mirror_s3(settings(), tmp_path / 'minio')
    client.objects['raw'] = {'stale': b'remove'}
    payload.mirror_s3(settings(), tmp_path / 'minio', restore=True)
    assert client.objects == original


def test_empty_sql_store_with_asset_references_is_rejected(monkeypatch):
    import sqlalchemy

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, statement):
            sql = str(statement)
            if 'FROM assets' in sql:
                return [('raw', 'missing.pdf')]
            return []

    engine = SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None)
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda _url: engine)
    monkeypatch.setattr(sqlalchemy, 'inspect', lambda _connection: SimpleNamespace(get_table_names=lambda: ['assets', 'object_blobs']))
    with pytest.raises(ValueError, match="1 referenced objects are missing from 'sql'"):
        payload.verify_references(settings())


def test_unpack_validation_preserves_live_files(tmp_path):
    import os
    import subprocess
    import sys
    import tarfile
    from scripts.snapshot_manifest import write_manifest

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / 'payload'
    (source / 'local/data').mkdir(parents=True)
    (source / 'local/data/__snapshot_validation_probe.txt').write_text('probe')
    write_manifest(source, 'test', 'test')
    archive = tmp_path / 'snapshot.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(source, arcname='.')
    result = subprocess.run(
        ['bash', str(root / 'scripts/unpack_data.sh'), str(archive), '--local-only', '--validate-only'],
        cwd=tmp_path, env={**os.environ, 'PYTHON': sys.executable},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert 'live data unchanged' in result.stdout
    assert not (root / 'data/__snapshot_validation_probe.txt').exists()


def test_unpack_blocks_backend_mismatch_before_bucket_replacement(tmp_path):
    import os
    import subprocess
    import sys
    import tarfile
    from scripts.snapshot_manifest import write_manifest

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / 'payload'
    (source / 'minio/raw').mkdir(parents=True)
    (source / 'snapshot.json').write_text('{"object_store_backend": "s3"}')
    write_manifest(source, 'test', 'test')
    archive = tmp_path / 'snapshot.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(source, arcname='.')
    result = subprocess.run(
        ['bash', str(root / 'scripts/unpack_data.sh'), str(archive), '--buckets-only', '--confirm-replace'],
        cwd=tmp_path, env={**os.environ, 'PYTHON': sys.executable, 'MKB_OBJECT_STORE_BACKEND': 'sql'},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert 'Configure MKB_OBJECT_STORE_BACKEND=s3' in result.stderr


def test_drill_settings_relocate_absolute_paths_and_preserve_bucket_names(tmp_path):
    import os
    import shlex
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / 'scripts/snapshot_payload.py'), 'settings'],
        cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH': str(root / 'src'),
             'MKB_OBJECT_STORE_BACKEND': 'file',
             'MKB_OBJECT_STORE_ROOT': str(tmp_path / 'objects'),
             'MKB_PROCESSED_LOCAL_ROOT': str(tmp_path / 'processed'),
             'MKB_S3_BUCKET_RAW': 'custom-raw'},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    values = dict(item.split('=', 1) for item in shlex.split(result.stdout))
    assert values['OBJECT_STORE_ROOT'] == 'objects'
    assert values['PROCESSED_LOCAL_ROOT'] == 'processed'
    assert values['BUCKET_RAW'] == 'custom-raw'
