from sqlalchemy import create_engine, text

from mkb.db.engine import (
    SchemaRevisionError,
    current_schema_revision,
    expected_schema_revision,
    require_schema_current,
)


def _sqlite_at_revision(revision: str | None):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    if revision is not None:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
    return engine


def test_schema_revision_accepts_the_single_alembic_head():
    expected = expected_schema_revision()
    engine = _sqlite_at_revision(expected)

    assert current_schema_revision(engine) == expected
    assert require_schema_current(engine) == expected


def test_schema_revision_rejects_empty_or_stale_database():
    engine = _sqlite_at_revision(None)

    try:
        require_schema_current(engine)
    except SchemaRevisionError as exc:
        assert "make migrate" in str(exc)
        assert "unversioned/empty" in str(exc)
    else:
        raise AssertionError("empty database should not pass the schema check")
