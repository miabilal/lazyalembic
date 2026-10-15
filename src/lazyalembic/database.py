"""Read which migrations a database has applied (one SELECT from alembic_version)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from lazyalembic.chain import Chain, ancestors

ENV_VAR = "LAZYALEMBIC_DATABASE_URL"
# what `alembic init` writes
_PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"
_CONNECT_TIMEOUT_SECONDS = 5
# module name -> package name, where they differ
_DRIVER_PACKAGES = {
    "MySQLdb": "mysqlclient",
    "psycopg2": "psycopg2-binary",
    "psycopg": "psycopg[binary]",
}


class DatabaseError(Exception):
    """The database couldn't be read. The message is safe to show."""


@dataclass(frozen=True)
class DatabaseState:
    heads: tuple[str, ...]
    # heads and everything they build on
    applied: frozenset[str]
    pending: tuple[str, ...]
    # on the database but not in the migrations folder
    unknown: tuple[str, ...]


def find_database_url(ini: Path | None, override: str | None = None) -> URL | None:
    """The database to read: ``--db``, then $LAZYALEMBIC_DATABASE_URL, then
    ``sqlalchemy.url`` in alembic.ini. None when none of them is set."""
    raw = override or os.environ.get(ENV_VAR)
    from_ini = False
    if not raw and ini is not None:
        raw = Config(str(ini)).get_main_option("sqlalchemy.url")
        from_ini = True
    if not raw or raw == _PLACEHOLDER_URL:
        return None

    try:
        url = make_url(raw)
    except ArgumentError:
        raise DatabaseError("The database address isn't a valid SQLAlchemy URL.") from None

    # relative SQLite paths in alembic.ini are relative to the ini
    if from_ini and url.get_backend_name() == "sqlite" and url.database:
        path = Path(url.database)
        if url.database != ":memory:" and not path.is_absolute():
            url = url.set(database=str(ini.parent / path))
    return url


def is_local(url: URL) -> bool:
    """True for a SQLite file or a server on this machine."""
    if url.get_backend_name() == "sqlite":
        return True
    return (url.host or "localhost") in ("localhost", "127.0.0.1", "::1")


def describe(url: URL) -> str:
    """A label for the screen, without user or password."""
    if url.get_backend_name() == "sqlite":
        return Path(url.database).name if url.database else "sqlite (in memory)"
    host = url.host or "localhost"
    return f"{host}/{url.database}" if url.database else host


def read_heads(url: URL) -> tuple[str, ...]:
    """The revision(s) recorded in the database's alembic_version table."""
    if url.get_backend_name() == "sqlite":
        # connecting would create the file
        if url.database and url.database != ":memory:" and not Path(url.database).exists():
            raise DatabaseError(f"The database file {url.database} doesn't exist.")
        connect_args = {}
    else:
        connect_args = {"connect_timeout": _CONNECT_TIMEOUT_SECONDS}

    try:
        engine = create_engine(url, connect_args=connect_args, poolclass=NullPool)
    except NoSuchModuleError:
        raise DatabaseError(
            f"lazyalembic doesn't know the database type {url.drivername!r}."
        ) from None
    except ModuleNotFoundError as exc:
        package = _DRIVER_PACKAGES.get(exc.name, exc.name)
        raise DatabaseError(
            f"The {exc.name} database driver isn't installed next to lazyalembic. "
            f"Add it with: uv tool install lazyalembic --with '{package}'"
        ) from None

    try:
        with engine.connect() as connection:
            return tuple(MigrationContext.configure(connection).get_current_heads())
    except SQLAlchemyError as exc:
        reason = str(getattr(exc, "orig", None) or exc).splitlines()[0]
        raise DatabaseError(f"Couldn't read {describe(url)}: {reason}") from None
    finally:
        engine.dispose()


def compare(chain: Chain, heads: tuple[str, ...]) -> DatabaseState:
    """Work out which chain migrations a database on ``heads`` has applied."""
    known = {m.revision for m in chain.migrations}
    unknown = tuple(h for h in heads if h not in known)
    applied: set[str] = set()
    for head in heads:
        applied |= ancestors(chain, head)

    pending = tuple(m.revision for m in chain.migrations if m.revision not in applied)
    return DatabaseState(
        heads=heads, applied=frozenset(applied), pending=pending, unknown=unknown
    )
