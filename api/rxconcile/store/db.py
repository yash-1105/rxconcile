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
        backfill(_engine)
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
    ("condition", "VARCHAR"),
    ("description", "VARCHAR"),
    ("decisions_json", "VARCHAR DEFAULT '{}'"),
    ("claimed_amount", "NUMERIC DEFAULT 0"),
    ("allowance_year", "VARCHAR DEFAULT ''"),
    ("first_name", "VARCHAR DEFAULT ''"),
    ("middle_name", "VARCHAR DEFAULT ''"),
    ("last_name", "VARCHAR DEFAULT ''"),
    ("certified_by_employee", "BOOLEAN DEFAULT 0"),
    ("certified_at", "DATETIME"),
    ("review_status", "VARCHAR DEFAULT 'submitted'"),
)


def split_name(full: str) -> tuple[str, str, str]:
    """A single typed name as (first, middle, last).

    Four or more words is ambiguous — a double-barrelled surname, a patronymic,
    an honorific — so the whole string goes in `first_name` rather than being
    carved up on a guess. The same rule the rest of this project follows: a
    stated value beats an inferred one, and no value beats a wrong one.
    """
    parts = full.split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return full.strip(), "", ""


def backfill(target: Engine) -> None:
    """Populate the columns `_add_missing_columns` could only declare.

    `ALTER TABLE ADD COLUMN` takes a constant default and nothing more, so the
    values that depend on the row have to be written here. Guarded on the
    column still being empty, which makes this idempotent: a second start
    changes nothing, and a row a human has since edited is never overwritten.
    """
    with target.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, employee_name, decisions_json, first_name, "
                "middle_name, last_name, review_status FROM scan_record"
            )
        ).all()
        for row in rows:
            scan_id, full, decisions, first, middle, last, status = row
            updates: dict[str, object] = {}
            # Only when no part has been set: an existing split wins over a
            # freshly computed one.
            if not (first or middle or last):
                parts = split_name(full or "")
                if any(parts):
                    updates["first_name"], updates["middle_name"], updates["last_name"] = parts
            if not status:
                # A scan somebody has already ruled on is reviewed; one nobody
                # has touched is still waiting. Nothing here is under review,
                # because until now there was no such state to be in.
                decided = bool((decisions or "{}").strip() not in {"", "{}"})
                updates["review_status"] = "reviewed" if decided else "submitted"
            if not updates:
                continue
            assignments = ", ".join(f"{key} = :{key}" for key in updates)
            connection.execute(
                text(f"UPDATE scan_record SET {assignments} WHERE id = :id"),
                {**updates, "id": scan_id},
            )
        logger.info("backfilled name parts and review status where empty")


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
