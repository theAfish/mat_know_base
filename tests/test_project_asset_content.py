from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from mkb.web.routers import projects


class _FakeQuery:
    def __init__(self, row):
        self.row = row

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row


class _FakeSession:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def query(self, *args):
        return _FakeQuery(self.row)


def test_get_project_asset_content_returns_inline_pdf(monkeypatch):
    row = SimpleNamespace(
        filename="论文.pdf",
        mime_type="application/pdf",
        s3_bucket="raw",
        s3_key="paper.pdf",
    )
    monkeypatch.setattr(projects, "SyncSessionLocal", lambda: _FakeSession(row))
    monkeypatch.setattr(projects, "download_bytes", lambda bucket, key: b"%PDF-test")

    response = projects.get_project_asset_content(str(uuid4()), str(uuid4()))

    assert response.body == b"%PDF-test"
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith('inline; filename=".pdf"')
    assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_get_project_processed_asset_content_returns_markdown(monkeypatch):
    row = SimpleNamespace(
        conversion_metadata={"primary_relpath": "paper.md"},
        output_format="md",
        s3_bucket="processed",
        s3_key="paper.md",
    )
    monkeypatch.setattr(projects, "SyncSessionLocal", lambda: _FakeSession(row))
    monkeypatch.setattr(projects, "download_bytes", lambda bucket, key: b"# Paper\n")

    response = projects.get_project_processed_asset_content(str(uuid4()), str(uuid4()))

    assert response.body == b"# Paper\n"
    assert response.media_type == "text/markdown"
    assert response.charset == "utf-8"


def test_get_project_asset_content_rejects_unlinked_asset(monkeypatch):
    monkeypatch.setattr(projects, "SyncSessionLocal", lambda: _FakeSession(None))

    with pytest.raises(HTTPException) as exc:
        projects.get_project_asset_content(str(uuid4()), str(uuid4()))

    assert exc.value.status_code == 404
