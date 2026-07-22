import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mkb import MKBConfig, PostProcessor, Skill
from mkb.web.routers import post_processor_scripts, skills


class _Service:
    def __init__(self, item=None):
        self.item = item
        self.deleted = []
        self.registered = None

    def list(self, *, limit):
        assert limit == 1000
        return [self.item] if self.item is not None else []

    def get(self, _identifier):
        return self.item

    def delete(self, identifier):
        self.deleted.append(identifier)

    def register(self, **values):
        self.registered = values
        return PostProcessor(
            id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            metadata={},
            **values,
        )

    def import_files(self, files):
        self.imported = files
        return self.item


def test_skill_routes_use_grouped_sdk_service(monkeypatch):
    skill = Skill(
        id=uuid.uuid4(),
        name="Existing skill",
        slug="existing-skill",
        content="# Existing",
    )
    service = _Service(skill)
    monkeypatch.setattr(
        skills,
        "get_knowledge_base",
        lambda: SimpleNamespace(skills=service),
    )

    assert skills.list_skills()[0]["skill_id"] == str(skill.id)
    assert skills.get_skill(skill.slug)["skill_md"] == skill.content
    assert skills.delete_skill(str(skill.id)) == {"ok": True, "deleted": skill.name}
    assert service.deleted == [skill.id]


@pytest.mark.asyncio
async def test_skill_upload_uses_grouped_sdk_import(monkeypatch):
    skill = Skill(
        id=uuid.uuid4(),
        name="Imported skill",
        slug="imported-skill",
        content="# Imported skill",
    )
    service = _Service(skill)
    monkeypatch.setattr(
        skills,
        "get_knowledge_base",
        lambda: SimpleNamespace(skills=service),
    )
    upload = SimpleNamespace(filename="SKILL.md", file=io.BytesIO(b"# Imported skill"))

    result = await skills.upload_skill([upload])

    assert result["skill_id"] == str(skill.id)
    assert result["skill_md"] == skill.content
    assert service.imported == [("SKILL.md", upload.file)]


@pytest.mark.asyncio
async def test_post_processor_routes_enforce_policy_and_use_sdk(monkeypatch):
    service = _Service()
    kb = SimpleNamespace(
        config=MKBConfig(allow_uploaded_python=True, upload_max_file_mb=1),
        post_processors=service,
    )
    monkeypatch.setattr(
        post_processor_scripts,
        "get_knowledge_base",
        lambda: kb,
    )
    upload = SimpleNamespace(filename="choose.py", file=io.BytesIO(b"print('{}')\n"))

    created = await post_processor_scripts.upload_post_processor_script(upload)

    assert created["filename"] == "choose.py"
    assert service.registered["source"] == "print('{}')\n"


@pytest.mark.asyncio
async def test_post_processor_upload_is_disabled_by_default(monkeypatch):
    kb = SimpleNamespace(
        config=MKBConfig(),
        post_processors=_Service(),
    )
    monkeypatch.setattr(
        post_processor_scripts,
        "get_knowledge_base",
        lambda: kb,
    )

    with pytest.raises(Exception) as exc:
        await post_processor_scripts.upload_post_processor_script(
            SimpleNamespace(filename="unsafe.py", file=io.BytesIO(b"print(1)"))
        )
    assert exc.value.status_code == 403
