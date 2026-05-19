from pathlib import Path


def test_api_run_upload_ingest_moves_files_from_session_temp(tmp_path, monkeypatch):
    import mkb.web.api_server as mod

    upload_id = "123e4567-e89b-12d3-a456-426614174000"
    temp_root = tmp_path / "_temp" / upload_id
    src_a = temp_root / "project_0" / "paper.pdf"
    src_b = temp_root / "project_1" / "paper.pdf"
    src_a.parent.mkdir(parents=True, exist_ok=True)
    src_b.parent.mkdir(parents=True, exist_ok=True)
    src_a.write_text("first")
    src_b.write_text("second")

    created_dirs: list[Path] = []

    def fake_create_unique_project_dir(project_name: str) -> Path:
        p = tmp_path / "uploads" / project_name
        p.mkdir(parents=True, exist_ok=True)
        created_dirs.append(p)
        return p

    ingested_dirs: list[Path] = []

    class DummyApi:
        @staticmethod
        def ingest(path: Path):
            ingested_dirs.append(Path(path))
            return {"ingested": 1, "duplicates": 0}

    monkeypatch.setattr(mod, "_UPLOAD_TEMP", tmp_path / "_temp")
    monkeypatch.setattr(mod, "_create_unique_project_dir", fake_create_unique_project_dir)
    monkeypatch.setattr(mod, "api", DummyApi)

    payload = [
        mod.UploadProject.model_validate(
            {
                "name": "project-a",
                "upload_id": upload_id,
                "files": [
                    {
                        "name": "paper.pdf",
                        "relativePath": "paper.pdf",
                        "uploadPath": "project_0/paper.pdf",
                    }
                ],
            }
        ),
        mod.UploadProject.model_validate(
            {
                "name": "project-b",
                "upload_id": upload_id,
                "files": [
                    {
                        "name": "paper.pdf",
                        "relativePath": "paper.pdf",
                        "uploadPath": "project_1/paper.pdf",
                    }
                ],
            }
        ),
    ]

    result = mod._run_upload_ingest(payload)

    assert result["created_projects"] == ["project-a", "project-b"]
    assert ingested_dirs == created_dirs
    assert (tmp_path / "uploads" / "project-a" / "paper.pdf").read_text() == "first"
    assert (tmp_path / "uploads" / "project-b" / "paper.pdf").read_text() == "second"
    assert not temp_root.exists()
