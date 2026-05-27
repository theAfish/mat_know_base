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
        def ingest(path: Path, **_kwargs):
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


def test_api_expand_extracts_archives_in_temp(tmp_path, monkeypatch):
    """``_expand_temp_dir`` extracts archives in place, removes them, and
    returns the resulting file tree. Used by ``POST /api/upload/expand``
    between the upload and ingest phases so the UI can let the user regroup
    files manually."""
    import io
    import zipfile
    import mkb.web.api_server as mod

    upload_id = "223e4567-e89b-12d3-a456-426614174000"
    temp_root = tmp_path / "_temp" / upload_id
    temp_root.mkdir(parents=True, exist_ok=True)

    # Archive with one good file, one zip-slip attempt, one __MACOSX entry
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("docs/paper.pdf", b"hello")
        zf.writestr("../evil.txt", b"nope")
        zf.writestr("__MACOSX/._meta", b"junk")
    (temp_root / "bundle.zip").write_bytes(buf.getvalue())
    # A non-archive file already in temp must appear in the listing untouched
    (temp_root / "loose.txt").write_text("loose")

    monkeypatch.setattr(mod, "_UPLOAD_TEMP", tmp_path / "_temp")

    result = mod._expand_temp_dir(temp_root)

    paths = {f["uploadPath"] for f in result["files"]}
    assert "loose.txt" in paths
    assert "bundle/docs/paper.pdf" in paths
    # Archive was removed and zip-slip entry was rejected
    assert not (temp_root / "bundle.zip").exists()
    assert not (tmp_path / "_temp" / "evil.txt").exists()
    assert not any("__MACOSX" in p for p in paths)
    assert result["extracted"] == [{"archive": "bundle.zip", "count": 1}]
    assert result["failed"] == []


def test_api_run_upload_ingest_honors_custom_grouping(tmp_path, monkeypatch):
    """``_run_upload_ingest`` no longer extracts archives — it just moves files
    according to the (possibly user-edited) project/relativePath mapping the
    UI sends after the expand+review step."""
    import mkb.web.api_server as mod

    upload_id = "323e4567-e89b-12d3-a456-426614174000"
    temp_root = tmp_path / "_temp" / upload_id
    # Simulate the file tree the UI saw after expand (already extracted)
    (temp_root / "bundle" / "docs").mkdir(parents=True)
    (temp_root / "bundle" / "docs" / "a.pdf").write_text("A")
    (temp_root / "bundle" / "docs" / "b.pdf").write_text("B")
    (temp_root / "loose.txt").write_text("loose")

    created_dirs: list[Path] = []

    def fake_create_unique_project_dir(project_name: str) -> Path:
        p = tmp_path / "uploads" / project_name
        p.mkdir(parents=True, exist_ok=True)
        created_dirs.append(p)
        return p

    ingested_dirs: list[Path] = []

    class DummyApi:
        @staticmethod
        def ingest(path: Path, **_kwargs):
            ingested_dirs.append(Path(path))
            return {"ingested": 1, "duplicates": 0}

    monkeypatch.setattr(mod, "_UPLOAD_TEMP", tmp_path / "_temp")
    monkeypatch.setattr(mod, "_create_unique_project_dir", fake_create_unique_project_dir)
    monkeypatch.setattr(mod, "api", DummyApi)

    # User chose "leaf folder" grouping: docs/ -> project "docs",
    # then moved loose.txt into the same project; b.pdf was excluded.
    payload = [
        mod.UploadProject.model_validate(
            {
                "name": "docs",
                "upload_id": upload_id,
                "files": [
                    {"name": "a.pdf", "relativePath": "a.pdf", "uploadPath": "bundle/docs/a.pdf"},
                    {"name": "loose.txt", "relativePath": "loose.txt", "uploadPath": "loose.txt"},
                ],
            }
        ),
    ]

    result = mod._run_upload_ingest(payload)

    proj_dir = tmp_path / "uploads" / "docs"
    assert result["created_projects"] == ["docs"]
    assert ingested_dirs == [proj_dir]
    assert (proj_dir / "a.pdf").read_text() == "A"
    assert (proj_dir / "loose.txt").read_text() == "loose"
    # Excluded file was not ingested
    assert not (proj_dir / "b.pdf").exists()


def test_api_run_upload_ingest_extracts_zip_archive(tmp_path, monkeypatch):
    """End-to-end: a raw archive in temp is expanded before ingest, and the
    UI's ingest call references the extracted files (not the archive)."""
    import io
    import zipfile
    import mkb.web.api_server as mod

    upload_id = "423e4567-e89b-12d3-a456-426614174000"
    temp_root = tmp_path / "_temp" / upload_id
    temp_root.mkdir(parents=True, exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("docs/paper.pdf", b"hello")
        zf.writestr("../evil.txt", b"nope")
    (temp_root / "bundle.zip").write_bytes(buf.getvalue())

    created_dirs: list[Path] = []

    def fake_create_unique_project_dir(project_name: str) -> Path:
        p = tmp_path / "uploads" / project_name
        p.mkdir(parents=True, exist_ok=True)
        created_dirs.append(p)
        return p

    ingested_dirs: list[Path] = []

    class DummyApi:
        @staticmethod
        def ingest(path: Path, **_kwargs):
            ingested_dirs.append(Path(path))
            return {"ingested": 1, "duplicates": 0}

    monkeypatch.setattr(mod, "_UPLOAD_TEMP", tmp_path / "_temp")
    monkeypatch.setattr(mod, "_create_unique_project_dir", fake_create_unique_project_dir)
    monkeypatch.setattr(mod, "api", DummyApi)

    # Step 1: expand (what the new POST /api/upload/expand does)
    expanded = mod._expand_temp_dir(temp_root)
    assert {f["uploadPath"] for f in expanded["files"]} == {"bundle/docs/paper.pdf"}

    # Step 2: ingest the expanded file under a user-chosen project name
    payload = [
        mod.UploadProject.model_validate(
            {
                "name": "bundle",
                "upload_id": upload_id,
                "files": [
                    {
                        "name": "paper.pdf",
                        "relativePath": "paper.pdf",
                        "uploadPath": "bundle/docs/paper.pdf",
                    }
                ],
            }
        ),
    ]

    result = mod._run_upload_ingest(payload)

    proj_dir = tmp_path / "uploads" / "bundle"
    assert result["created_projects"] == ["bundle"]
    assert ingested_dirs == [proj_dir]
    assert (proj_dir / "paper.pdf").read_bytes() == b"hello"
    # Zip-slip entry was skipped during expansion
    assert not (tmp_path / "_temp" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
