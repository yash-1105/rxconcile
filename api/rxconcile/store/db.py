"""SQLite engine and session handling.

The database file is local demo storage. It is gitignored and holds nothing that
should outlive a demonstration.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, create_engine

logger: Final = logging.getLogger(__name__)

DB_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "api" / "data" / "rxconcile.db"

_engine: Engine | None = None


def engine() -> Engine:
    """Process-wide engine, created on first use."""
    global _engine
    if _engine is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
        SQLModel.metadata.create_all(_engine)
        _add_missing_columns(_engine)
        logger.info("scan store ready at %s", DB_PATH)
    return _engine


#: Columns added after the table first shipped. ``create_all`` only creates
#: tables that do not exist, so an existing demo database keeps its old shape
#: and every read of a new column fails. This is the whole migration story the
#: demo needs: additive, nullable, and safe to run on every start.
_ADDED_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("prescription_image", "BLOB"),
    ("bill_image", "BLOB"),
    ("image_media_type", "VARCHAR DEFAULT 'image/jpeg'"),
)


def _add_missing_columns(target: Engine) -> None:
    with target.begin() as connection:
        existing = {
            row[1] for row in connection.execute(text("PRAGMA table_info(scan_record)"))
        }
        for name, ddl in _ADDED_COLUMNS:
            if name in existing:
                continue
            connection.execute(text(f"ALTER TABLE scan_record ADD COLUMN {name} {ddl}"))
            logger.info("added scan_record.%s to the existing database", name)


def set_engine(replacement: Engine | None) -> None:
    """Point the store at another engine. Used by tests to stay off disk."""
    global _engine
    _engine = replacement


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
