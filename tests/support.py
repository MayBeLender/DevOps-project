import importlib
import os
from pathlib import Path
from unittest.mock import Mock, patch


with patch("dotenv.load_dotenv"), patch.dict(os.environ, {"HOST": "127.0.0.1", "PORT": "8000"}):
    main = importlib.import_module("main")


def migrate_schema(database_settings, schema):
    import psycopg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(
            **database_settings, options=f"-c search_path={schema}",
        ),
    )
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def make_handler(payload=None):
    handler = main.BareSiteHandler.__new__(main.BareSiteHandler)
    handler.body = Mock(return_value=payload if payload is not None else {})
    handler.json = Mock()
    return handler
