from pathlib import Path

from mkb.ui.pages.projects import (
    _create_unique_project_dir,
    _next_available_path,
    _normalize_project_name,
)


def test_normalize_strips_special_chars():
    # "!" → "_", then trailing "_" stripped
    assert _normalize_project_name("Hello World!", fallback="fb") == "Hello World"


def test_normalize_uses_fallback_when_blank():
    assert _normalize_project_name("  ", fallback="my_project") == "my_project"


def test_normalize_file_stem():
    assert _normalize_project_name(Path("paper.pdf").stem, fallback="project") == "paper"


def test_normalize_folder_name():
    assert _normalize_project_name("Study Set 2024", fallback="project") == "Study Set 2024"


def test_next_available_path_no_collision(tmp_path):
    p = tmp_path / "file.txt"
    assert _next_available_path(p) == p


def test_next_available_path_collision(tmp_path):
    p = tmp_path / "file.txt"
    p.write_text("x")
    assert _next_available_path(p) == tmp_path / "file_2.txt"


def test_next_available_path_multiple_collisions(tmp_path):
    for name in ("file.txt", "file_2.txt"):
        (tmp_path / name).write_text("x")
    assert _next_available_path(tmp_path / "file.txt") == tmp_path / "file_3.txt"


def test_create_unique_project_dir(tmp_path, monkeypatch):
    import mkb.ui.pages.projects as mod
    monkeypatch.setattr(mod, "_UPLOAD_ROOT", tmp_path)
    d = _create_unique_project_dir("my-project")
    assert d.name == "my-project"
    assert d.is_dir()


def test_create_unique_project_dir_collision(tmp_path, monkeypatch):
    import mkb.ui.pages.projects as mod
    monkeypatch.setattr(mod, "_UPLOAD_ROOT", tmp_path)
    (tmp_path / "my-project").mkdir()
    d = _create_unique_project_dir("my-project")
    assert d.name == "my-project_2"
    assert d.is_dir()


def test_component_dir_exists():
    """The folder_drop_zone component directory must exist and contain index.html."""
    from mkb.ui.pages.projects import _COMPONENT_DIR
    assert _COMPONENT_DIR.is_dir(), f"Component dir missing: {_COMPONENT_DIR}"
    assert (_COMPONENT_DIR / "index.html").is_file()
