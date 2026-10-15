# lazyalembic

A terminal map of your Alembic migrations.

```sh
lazyalembic                      # in your project folder
lazyalembic path/to/project      # or a migrations folder, or an alembic.ini
```

- See your migration chain at a glance, newest first, with heads, splits
  and merges marked
- Migration files are read, never run: works even when they import app code
  that isn't installed, and opening someone else's repo can't run its code
- See which migrations your database has applied (✓) and which it hasn't (·)

The database is read once, with a single SELECT from `alembic_version`. It
comes from `--db URL`, else `$LAZYALEMBIC_DATABASE_URL`, else `sqlalchemy.url`
in alembic.ini. Passwords are never shown. MySQL and Postgres need their
driver installed next to the tool, e.g. `uv tool install lazyalembic --with pymysql`.

- A split chain ("multiple heads") is called out before it breaks a deploy
- Change the database from the map, seeing the exact command first:
  - `u` upgrade to the selected migration
  - `d` downgrade to the selected migration
  - `m` join a split chain with a merge migration
  - `r` re-read the files and the database

Changes run your project's own command in your project's folder and
virtualenv: `flask db …` for Flask-Migrate projects, `alembic …` otherwise.
Every change shows the command and asks first. Upgrades and downgrades only
run on a database on this computer, and only after the project's own
`current` command agrees with lazyalembic about which migration that
database is on, so a project configured for another database is caught
before anything runs.

On MySQL projects, each migration's `upgrade()` is checked for steps that
are risky on a big table, with a plain-English reason and a safer way:
blocks writes (MySQL copies the table: column type changes, foreign keys,
FULLTEXT indexes, dropping a primary key…), deletes data (dropped columns
and tables), rebuilds table (NULL / NOT NULL changes, new primary keys),
check SQL (raw `ALTER TABLE`), and slow (index builds, big `UPDATE`s). The
upgrade confirm box lists the serious ones before anything runs. Rules
follow the MySQL 8.0 manual's online DDL tables and assume MySQL 8.0+ with
utf8mb4. Tables created in the same migration are empty and skipped. Only
operations written directly in `upgrade()` are checked, not ones inside
helper functions it calls.

Planned:

- See which migration staging and live are on, too

> Early days: everything above "Planned" works; the rest is still to come.

## Development

```sh
uv sync                         # install dependencies
uv run lazyalembic playground   # open the app on the practice project (q to quit)
uv run pytest                   # run the tests
```

To use the `lazyalembic` command from any folder while developing, install it
in editable mode (code changes apply without reinstalling):

```sh
uv tool install --editable .
uv tool update-shell            # once, if ~/.local/bin isn't on your PATH
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
