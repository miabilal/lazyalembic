# lazyalembic

A terminal UI for Alembic migrations. See the chain as a graph, which
migrations your database has applied, and which steps would lock a big table
on MySQL. Upgrade, downgrade and merge from the same screen, with the exact
command shown before anything runs.

```
 Database 127.0.0.1/shop is on c3d5e4f6a7b8 · 2 migrations not applied yet
 ⚠ The chain has split into 2 ends:
     d4e6f5a7b8c9  Add brand slug to products
     e5f7a6b8c9d0  Make orders.notes TEXT
 Upgrading to "head" fails until they are joined by a merge migration.
 ──────────────────────────────────────────────────────────────────────────
            DB  MySQL risk     Revision      Date        Message
 ●    head  ·   slow           d4e6f5a7b8c9  2026-10-01  Add brand slug to products
 │ ●  head  ·   blocks writes  e5f7a6b8c9d0  2026-10-02  Make orders.notes TEXT
 ●─╯  split ✓                  c3d5e4f6a7b8  2026-09-12  Add gift wrap to orders
 ●          ✓                  b2e4d3c5f6a7  2026-09-05  Create orders
 ●    first ✓                  a1f3c2d4e5b6  2026-09-01  Create products
 u Upgrade to here  d Downgrade to here  m Join ends  r Refresh  q Quit
```

## Install

```sh
pipx install lazyalembic          # or: uv tool install lazyalembic
```

To read a MySQL or Postgres database, install its driver next to the tool:

```sh
uv tool install lazyalembic --with pymysql             # MySQL
uv tool install lazyalembic --with 'psycopg[binary]'   # Postgres
pipx inject lazyalembic pymysql                         # same, with pipx
```

Needs Python 3.10 or newer. Tested on macOS and Linux.

## Use

Run it in your project folder, or point it at one:

```sh
lazyalembic
lazyalembic path/to/project          # a project folder, migrations folder or alembic.ini
lazyalembic --db mysql+pymysql://root@127.0.0.1/shop
```

Plain Alembic and Flask-Migrate layouts are both found.

| Key | Does |
| --- | --- |
| ↑ ↓ | move between migrations |
| u | upgrade the database to the selected migration |
| d | downgrade the database to the selected migration |
| m | merge the heads of a split chain into one |
| r | re-read the migration files and the database |
| q | quit |

## What it shows

**The chain.** Newest first, drawn like `git log --graph`, with heads,
splits, merges and the first migration marked. More than one head gets a
warning at the top, since `alembic upgrade head` fails until they're merged.

**The database.** The DB column marks applied migrations ✓ and pending ones
·. The database is read with a single `SELECT` from `alembic_version`, from
the first of these that is set:

1. `--db URL`
2. `$LAZYALEMBIC_DATABASE_URL`
3. `sqlalchemy.url` in alembic.ini

Passwords are never shown. A missing SQLite file is reported, not created.

**MySQL risks.** On MySQL projects each migration's `upgrade()` is checked
against the MySQL 8.0 manual's online DDL tables. Select a row to see the
reason and a safer way to do it.

| Label | Examples |
| --- | --- |
| blocks writes | changing a column's type, adding a foreign key, FULLTEXT index, dropping the primary key: MySQL copies the table and writes wait |
| deletes data | dropping a column or table |
| rebuilds | making a column NULL or NOT NULL, adding a primary key |
| check SQL | raw `ALTER TABLE` in `op.execute` |
| slow | building an index, `UPDATE`/`DELETE` in one statement |

Growing a `VARCHAR` stays in place unless it crosses 255 bytes, and
appending `ENUM` values is instant; both are recognised when `existing_type`
is given. Steps on a table created in the same migration are skipped, since
it's empty. The checks are on when the database is MySQL or MariaDB, or,
with no database set, when the migrations use MySQL-specific features.

## Changing the database

`u`, `d` and `m` run your project's own command in its folder and
virtualenv: `flask db upgrade …` for Flask-Migrate projects, `alembic -c
alembic.ini upgrade …` otherwise. A confirm box lists what will be applied
or undone, any serious MySQL risks, and the exact command.

Because that command uses your project's own database settings, upgrade and
downgrade only run when:

- the database lazyalembic is showing is on this computer, and
- your project's own `current` command reports the same revision.

If they disagree, nothing runs. Merging only writes a new migration file.

## Limitations

- Migration files are read, never imported, so `revision` and
  `down_revision` must be plain values.
- MySQL rules assume MySQL 8.0+ with utf8mb4. Only operations written
  directly in `upgrade()` are checked, not ones inside helper functions.
- `script_location` in `package:path` form isn't supported.

## Development

```sh
uv sync
uv run lazyalembic playground    # practice project with a split chain
uv run pytest
uv tool install --editable .     # use your working copy as the lazyalembic command
```

`playground/` is a small SQLite-backed Alembic project. Two of its
migrations build on the same parent, so its chain has two heads.

## License

MIT
