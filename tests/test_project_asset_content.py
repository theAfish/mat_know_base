from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from mkb.web.routers import projects


class _FakeContentService:
    def __init__(self, row, content):
        self.row = row
        self.content = content

    def get(self, _identifier):
        return self.row

    def read_bytes(self, _identifier):
        return self.content


def _kb(*, source=None, artifact=None, content=b""):
    return SimpleNamespace(
        sources=_FakeContentService(source, content),
        artifacts=_FakeContentService(artifact, content),
    )


def test_get_project_asset_content_returns_inline_pdf(monkeypatch):
    project_id = uuid4()
    row = SimpleNamespace(
        id=uuid4(),
        filename="论文.pdf",
        media_type="application/pdf",
        collection_ids=(project_id,),
    )
    monkeypatch.setattr(
        projects,
        "get_knowledge_base",
        lambda: _kb(source=row, content=b"%PDF-test"),
    )

    response = projects.get_project_asset_content(str(project_id), str(row.id))

    assert response.body == b"%PDF-test"
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith('inline; filename=".pdf"')
    assert "filename*=UTF-8''" in response.headers["content-disposition"]


def test_get_project_processed_asset_content_returns_markdown(monkeypatch):
    project_id = uuid4()
    source = SimpleNamespace(id=uuid4(), collection_ids=(project_id,))
    artifact = SimpleNamespace(
        id=uuid4(),
        source_id=source.id,
        primary_path="paper.md",
        format="md",
    )
    monkeypatch.setattr(
        projects,
        "get_knowledge_base",
        lambda: _kb(source=source, artifact=artifact, content=b"# Paper\n"),
    )

    response = projects.get_project_processed_asset_content(
        str(project_id), str(artifact.id)
    )

    assert response.body == b"# Paper\n"
    assert response.media_type == "text/markdown"
    assert response.charset == "utf-8"


def test_get_project_asset_content_rejects_unlinked_asset(monkeypatch):
    monkeypatch.setattr(
        projects,
        "get_knowledge_base",
        lambda: _kb(source=None),
    )

    with pytest.raises(HTTPException) as exc:
        projects.get_project_asset_content(str(uuid4()), str(uuid4()))

    assert exc.value.status_code == 404
