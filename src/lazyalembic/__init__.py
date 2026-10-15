"""lazyalembic: a terminal map of your Alembic migrations."""

import argparse
import sys
from pathlib import Path

__version__ = "0.1.0"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="lazyalembic",
        description="A terminal map of your Alembic migrations.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="project folder, migrations folder, or alembic.ini (default: current folder)",
    )
    parser.add_argument(
        "--db",
        metavar="URL",
        help="database to compare against, as a SQLAlchemy URL (default: "
        "$LAZYALEMBIC_DATABASE_URL, then sqlalchemy.url in alembic.ini)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    from lazyalembic.app import LazyAlembicApp
    from lazyalembic.chain import ChainError, find_ini, load_chain
    from lazyalembic.database import DatabaseError, find_database_url

    path = Path(args.path)
    try:
        chain = load_chain(path)
        database_url = find_database_url(find_ini(path), override=args.db)
    except (ChainError, DatabaseError) as exc:
        print(f"lazyalembic: {exc}", file=sys.stderr)
        sys.exit(1)

    LazyAlembicApp(chain, database_url, start=path).run()
