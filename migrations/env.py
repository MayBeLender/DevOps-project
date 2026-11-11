import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

def database_url():
    # Match main.py: settings from the project's .env take precedence.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing database environment variables: " + ", ".join(missing))
    return URL.create(
        "postgresql+psycopg",
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )

def run_migrations(connection):
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()

if context.is_offline_mode():
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=None,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
    else:
        engine = create_engine(
            database_url(), poolclass=pool.NullPool,
            connect_args={"connect_timeout": 3},
        )
        try:
            with engine.connect() as connection:
                run_migrations(connection)
        finally:
            engine.dispose()
