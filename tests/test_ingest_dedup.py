import uuid
from types import SimpleNamespace

from mkb.db.models import Asset, ProjectAsset, ResearchProject
from mkb.ingest import worker


class _Query:
    def __init__(self, *, first=None, rows=None):
        self._first = first
        self._rows = rows or []

    def filter_by(self, **_kwargs):
        return self

    def filter(self, *_args):
        return self

    def group_by(self, *_args):
        return self

    def having(self, *_args):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._rows


class _Session:
    def __init__(self, asset, existing_project_id):
        self.asset = asset
        self.existing_project_id = existing_project_id
        self.added = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def query(self, target):
        if target is ResearchProject:
            return _Query(first=None)
        if target is Asset:
            return _Query(rows=[self.asset])
        if target is ProjectAsset.project_id:
            return _Query(rows=[SimpleNamespace(project_id=self.existing_project_id)])
        raise AssertionError(f"Unexpected query target: {target!r}")

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.committed = True


def test_repeated_project_reuses_existing_project_without_creating_row(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 repeated content")

    asset_id = uuid.uuid4()
    existing_project_id = uuid.uuid4()
    digest = worker.sha256_file(pdf)
    asset = SimpleNamespace(asset_id=asset_id, sha256=digest)
    session = _Session(asset, existing_project_id)

    monkeypatch.setattr(worker, "SyncSessionLocal", lambda: session)

    result = worker.ingest_directory(tmp_path)

    assert result == {
        "total": 1,
        "ingested": 0,
        "duplicates": 1,
        "errors": 0,
        "project_id": str(existing_project_id),
        "duplicate_of": str(existing_project_id),
        "project_reused": True,
    }
    assert session.added == []
    assert not session.committed


def test_upload_handler_removes_directory_for_reused_project(tmp_path, monkeypatch):
    import mkb.web.api_server as api_server

    upload_id = "123e4567-e89b-12d3-a456-426614174000"
    temp_root = tmp_path / "_temp" / upload_id
    source = temp_root / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF duplicate")
    upload_dir = tmp_path / "uploads" / "paper"

    class _Api:
        @staticmethod
        def ingest(_path, **_kwargs):
            return {"ingested": 0, "duplicates": 1, "project_reused": True}

    def create_project_dir(_name):
        upload_dir.mkdir(parents=True)
        return upload_dir

    monkeypatch.setattr(api_server, "_UPLOAD_TEMP", tmp_path / "_temp")
    monkeypatch.setattr(api_server, "_create_unique_project_dir", create_project_dir)
    monkeypatch.setattr(
        "mkb.web.dependencies.get_knowledge_base", lambda: _Api
    )

    payload = [
        api_server.UploadProject.model_validate(
            {
                "name": "paper",
                "upload_id": upload_id,
                "files": [
                    {
                        "name": "paper.pdf",
                        "relativePath": "paper.pdf",
                        "uploadPath": "paper.pdf",
                    }
                ],
            }
        )
    ]
    result = api_server._run_upload_ingest(payload)

    assert result["created_projects"] == []
    assert result["reused_projects"] == 1
    assert not upload_dir.exists()
