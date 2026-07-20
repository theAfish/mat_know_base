from pathlib import Path

import pytest
from sqlalchemy import text

from mkb.adapters import FileObjectStore, SQLAlchemyDatabase


def test_sqlalchemy_databases_are_isolated_and_transactional(tmp_path: Path):
    first = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'first.db'}")
    second = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'second.db'}")
    try:
        with first.transaction() as session:
            session.execute(text("create table values_table (value text not null)"))
            session.execute(text("insert into values_table values ('first')"))
        with second.transaction() as session:
            session.execute(text("create table values_table (value text not null)"))
            session.execute(text("insert into values_table values ('second')"))

        with first.session() as session:
            assert session.execute(text("select value from values_table")).scalar_one() == "first"
        with second.session() as session:
            assert session.execute(text("select value from values_table")).scalar_one() == "second"
    finally:
        first.close()
        second.close()


def test_sqlalchemy_transaction_rolls_back(tmp_path: Path):
    database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'rollback.db'}")
    try:
        with database.transaction() as session:
            session.execute(text("create table values_table (value text not null)"))
        with pytest.raises(RuntimeError, match="stop"):
            with database.transaction() as session:
                session.execute(text("insert into values_table values ('no')"))
                raise RuntimeError("stop")
        with database.session() as session:
            assert session.execute(text("select count(*) from values_table")).scalar_one() == 0
    finally:
        database.close()


def test_file_object_stores_are_isolated_and_reject_unsafe_keys(tmp_path: Path):
    first = FileObjectStore(tmp_path / "first")
    second = FileObjectStore(tmp_path / "second")
    first.put_bytes("raw", "papers/a.txt", b"first")
    second.put_bytes("raw", "papers/a.txt", b"second")

    assert first.get_bytes("raw", "papers/a.txt") == b"first"
    assert second.get_bytes("raw", "papers/a.txt") == b"second"
    assert [item.key for item in first.list("raw", "papers/")] == ["papers/a.txt"]
    with pytest.raises(ValueError, match="Unsafe"):
        first.put_bytes("raw", "../escape", b"bad")
