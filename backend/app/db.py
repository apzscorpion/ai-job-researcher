"""
Database engine, session dependency, and table creation for AI Job Researcher.
"""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def get_session():
    """FastAPI dependency that yields a database session."""
    with Session(engine) as session:
        yield session


def _sql_literal(value) -> str | None:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def add_missing_columns(eng: Engine) -> None:
    """Add model columns that an older database file does not have yet.

    shortcut: only adds new columns; renames or type changes need Alembic.
    """
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                default = getattr(col.default, "arg", None)
                literal = None if callable(default) else _sql_literal(default)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(eng.dialect)}'
                if literal is not None:
                    ddl += f" DEFAULT {literal}"
                conn.exec_driver_sql(ddl)


def create_db_and_tables() -> None:
    """Create all SQLModel tables and add new columns. Called once at startup."""
    import app.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
