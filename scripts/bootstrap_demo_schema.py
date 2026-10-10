"""Create a disposable local MKB schema for integration acceptance only."""

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from mkb.config import settings
from mkb.db.models import Base
from mkb import KnowledgeBase


DEMO_DATABASE = "mkb_demo_acceptance"


def main() -> None:
    if settings.pg_database != DEMO_DATABASE or settings.pg_host not in {"localhost", "127.0.0.1"}:
        raise SystemExit(f"Refusing to initialize outside local {DEMO_DATABASE}")

    url = make_url(settings.pg_dsn_sync)
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": DEMO_DATABASE})
            if not exists:
                connection.exec_driver_sql(f'CREATE DATABASE "{DEMO_DATABASE}"')
    finally:
        admin_engine.dispose()

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.create_all(engine, checkfirst=True)
    finally:
        engine.dispose()

    with KnowledgeBase.from_url(database_url=settings.pg_dsn_sync) as knowledge_base:
        knowledge_base.initialize()
        knowledge_base.initialize_knowledge()
    print(f"Initialized local demo schema in {DEMO_DATABASE}")


if __name__ == "__main__":
    main()