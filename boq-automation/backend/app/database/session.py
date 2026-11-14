from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")
if is_sqlite:
    Path(settings.database_url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, future=True, pool_pre_ping=True,
                       connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {})

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Create missing tables, then add columns that newer versions introduced to existing tables.

    create_all never alters an existing table, so an older database would otherwise fail on
    the first query that touches a new column. Only additive changes are made.
    """
    import app.models.tables  # noqa: F401  (registers every table on Base.metadata)

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                kind = col.type.compile(dialect=engine.dialect)
                default = col.default.arg if col.default is not None else None
                if callable(default):
                    probe = default.__name__ if hasattr(default, "__name__") else ""
                    default = "[]" if probe == "list" else "{}" if probe == "dict" else None
                if isinstance(default, bool):
                    literal = "TRUE" if default else "FALSE"  # valid in PostgreSQL and SQLite
                elif isinstance(default, (int, float)):
                    literal = str(default)
                elif isinstance(default, str):
                    literal = "'" + default.replace("'", "''") + "'"
                else:
                    literal = None
                clause = f" DEFAULT {literal}" if literal is not None else ""
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {kind}{clause}'))
