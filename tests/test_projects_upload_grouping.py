from pathlib import Path

from mkb.services.project_names import (
    create_unique_project_dir,
    next_available_path,
    normalize_project_name,
)


def test_normalize_strips_special_chars():
    # "!" → "_", then trailing "_" stripped
    assert normalize_project_name("Hello World!", fallback="fb") == "Hello World"


def test_normalize_uses_fallback_when_blank():
    assert normalize_project_name("  ", fallback="my_project") == "my_project"


def test_normalize_file_stem():
    assert normalize_project_name(Path("paper.pdf").stem, fallback="project") == "paper"


def test_normalize_folder_name():
    assert normalize_project_name("Study Set 2024", fallback="project") == "Study Set 2024"


def test_next_available_path_no_collision(tmp_path):
    p = tmp_path / "file.txt"
    assert next_available_path(p) == p


def test_next_available_path_collision(tmp_path):
    p = tmp_path / "file.txt"
    p.write_text("x")
    assert next_available_path(p) == tmp_path / "file_2.txt"


def test_next_available_path_multiple_collisions(tmp_path):
    for name in ("file.txt", "file_2.txt"):
        (tmp_path / name).write_text("x")
    assert next_available_path(tmp_path / "file.txt") == tmp_path / "file_3.txt"


def test_create_unique_project_dir(tmp_path, monkeypatch):
    d = create_unique_project_dir(tmp_path, "my-project")
    assert d.name == "my-project"
    assert d.is_dir()


def test_create_unique_project_dir_collision(tmp_path, monkeypatch):
    (tmp_path / "my-project").mkdir()
    d = create_unique_project_dir(tmp_path, "my-project")
    assert d.name == "my-project_2"
    assert d.is_dir()


def test_unique_dirs_separate_projects_with_same_file_names(tmp_path):
    first = create_unique_project_dir(tmp_path, "project")
    second = create_unique_project_dir(tmp_path, "project")
    assert (first.name, second.name) == ("project", "project_2")
