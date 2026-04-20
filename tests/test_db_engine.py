from sqlalchemy import create_engine, inspect, text

from mkb.db.engine import _apply_schema_compatibility


def test_apply_schema_compatibility_adds_missing_extraction_version():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE knowledge_frames (
                    frame_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content TEXT,
                    extraction_summary TEXT,
                    times_checked INTEGER NOT NULL DEFAULT 0,
                    extracted_at TEXT,
                    source_metadata TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )

    _apply_schema_compatibility(engine)

    columns = {col["name"] for col in inspect(engine).get_columns("knowledge_frames")}
    assert "extraction_version" in columns


def test_apply_schema_compatibility_rebuilds_projection_index_without_uniqueness():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE projections (
                    projection_id TEXT PRIMARY KEY,
                    space_id TEXT NOT NULL,
                    frame_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT,
                    validation_result TEXT,
                    agent_notes TEXT,
                    extracted_at TEXT,
                    space_version INTEGER NOT NULL,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_projection_space_frame ON projections (space_id, frame_id)"
            )
        )

    _apply_schema_compatibility(engine)

    indexes = {
        index["name"]: index
        for index in inspect(engine).get_indexes("projections")
    }
    assert "ix_projection_space_frame" in indexes
    assert not indexes["ix_projection_space_frame"]["unique"]
