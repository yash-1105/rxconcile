"""SQLite engine and session handling.

The database file is local demo storage. It is gitignored and holds nothing that
should outlive a demonstration.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from sqlalchemy import Engine
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
        logger.info("scan store ready at %s", DB_PATH)
    return _engine


def set_engine(replacement: Engine | None) -> None:
    """Point the store at another engine. Used by tests to stay off disk."""
    global _engine
    _engine = replacement


def get_session() -> Iterator[Session]:
    with Session(engine()) as session:
        yield session
