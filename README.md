# lazyalembic

A terminal map of your Alembic migrations.

Planned:

- See your migration chain at a glance
- See which migration each database is on (laptop, staging, live)
- Spot a split chain ("multiple heads") before it breaks a deploy
- Get warned about MySQL changes that copy or lock big tables
- See the exact `alembic` command behind every action

> Early days: only the empty screen exists so far.

## Development

```sh
uv sync                 # install dependencies
uv run lazyalembic      # open the app (q to quit)
uv run pytest           # run the tests
```

## Playground

`playground/` is a small practice Alembic project backed by SQLite, so the
tool can be tried without touching a real database. Its chain splits on
purpose: two migrations both build on "add gift wrap to orders", the way it
happens when two developers branch at the same time.

```sh
uv run alembic -c playground/alembic.ini heads     # shows the two heads
uv run alembic -c playground/alembic.ini upgrade heads
```

The SQLite file it creates (`playground/playground.db`) is git-ignored.
