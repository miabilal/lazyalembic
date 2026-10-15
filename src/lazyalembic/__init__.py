"""lazyalembic: a terminal map of your Alembic migrations."""

__version__ = "0.1.0"


def main() -> None:
    from lazyalembic.app import LazyAlembicApp

    LazyAlembicApp().run()
