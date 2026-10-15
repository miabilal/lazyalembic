import pytest
from conftest import PLAYGROUND, write_migration
from sqlalchemy.engine import make_url
from textual.widgets import DataTable, Static

from lazyalembic import main
from lazyalembic.app import LazyAlembicApp
from lazyalembic.chain import load_chain


async def test_shows_the_playground_chain():
    app = LazyAlembicApp(load_chain(PLAYGROUND))
    async with app.run_test():
        table = app.query_one("#chain", DataTable)
        assert table.row_count == 5
        assert app.sub_title == "playground · 5 migrations · 2 heads"
        assert table.get_row("c3d5e4f6a7b8")[1] == "split"
        assert table.get_row("d4e6f5a7b8c9")[1] == "head"
        assert table.get_row("a1f3c2d4e5b6")[1] == "first"
        assert table.get_row("e5f7a6b8c9d0")[0].plain == "│ ●"
        assert app.query_one("#problems", Static).display is False


async def test_split_chain_shows_a_warning_naming_both_ends():
    app = LazyAlembicApp(load_chain(PLAYGROUND))
    async with app.run_test():
        split = app.query_one("#split", Static)
        assert split.display is True
        text = str(split.render())
        assert "split into 2 ends" in text
        assert "d4e6f5a7b8c9  Add brand slug to products" in text
        assert "e5f7a6b8c9d0  Add notes to orders" in text


async def test_single_head_shows_no_split_warning(project):
    write_migration(project, "aaa", None)
    write_migration(project, "bbb", "aaa")
    app = LazyAlembicApp(load_chain(project))
    async with app.run_test():
        assert app.query_one("#split", Static).display is False


async def test_problems_are_listed_under_the_chain(project):
    write_migration(project, "aaa", None)
    (project / "migrations" / "versions" / "broken.py").write_text("revision = (\n")

    app = LazyAlembicApp(load_chain(project))
    async with app.run_test():
        problems = app.query_one("#problems", Static)
        assert problems.display is True
        assert "broken.py" in str(problems.render())
        assert app.sub_title.endswith("1 migration · 1 head")


async def test_q_quits():
    app = LazyAlembicApp(load_chain(PLAYGROUND))
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.return_code == 0


def test_cli_explains_when_there_are_no_migrations(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path)])
    assert exit_info.value.code == 1
    assert "No Alembic migrations found" in capsys.readouterr().err


async def settle(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_db_column_marks_applied_migrations(tmp_path):
    from test_database import sqlite_db

    url = sqlite_db(tmp_path / "laptop.db", "c3d5e4f6a7b8")
    app = LazyAlembicApp(load_chain(PLAYGROUND), url)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        table = app.query_one("#chain", DataTable)
        assert table.get_row("a1f3c2d4e5b6")[2].plain == "✓"
        assert table.get_row("c3d5e4f6a7b8")[2].plain == "✓"
        assert table.get_row("d4e6f5a7b8c9")[2].plain == "·"
        assert table.get_row("e5f7a6b8c9d0")[2].plain == "·"
        status = str(app.query_one("#database", Static).render())
        assert "laptop.db is on c3d5e4f6a7b8 · 2 migrations not applied yet" in status


async def test_db_problem_is_shown_without_crashing(tmp_path):
    from sqlalchemy.engine import make_url

    app = LazyAlembicApp(load_chain(PLAYGROUND), make_url(f"sqlite:///{tmp_path}/gone.db"))
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "doesn't exist" in str(app.query_one("#database", Static).render())
        assert app.query_one("#chain", DataTable).row_count == 5


async def test_without_a_database_there_is_no_db_column():
    app = LazyAlembicApp(load_chain(PLAYGROUND))
    async with app.run_test():
        table = app.query_one("#chain", DataTable)
        assert "db" not in [str(key.value) for key in table.columns]
        assert "No database set" in str(app.query_one("#database", Static).render())


RISKY = '''"""Change notes type

Revision ID: bbb
Revises: aaa
Create Date: 2026-10-02 10:00:00

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "bbb"
down_revision = "aaa"


def upgrade():
    op.alter_column("orders", "notes", existing_type=mysql.VARCHAR(50), type_=sa.Text())
    op.drop_column("orders", "legacy")


def downgrade():
    pass
'''


async def test_mysql_risks_show_in_a_column_and_explain_the_selected_row(project):
    write_migration(project, "aaa", None)
    (project / "migrations" / "versions" / "bbb_change.py").write_text(RISKY)

    app = LazyAlembicApp(load_chain(project))
    async with app.run_test() as pilot:
        table = app.query_one("#chain", DataTable)
        risk_col = [str(c.value) for c in table.columns].index("risk")
        assert table.get_row("bbb")[risk_col].plain == "blocks writes +1"
        assert table.get_row("aaa")[risk_col].plain == ""

        table.move_cursor(row=table.get_row_index("bbb"))
        await pilot.pause()
        details = app.query_one("#details", Static)
        assert details.display is True
        text = str(details.render())
        assert "Changes the type of orders.notes (line 17)" in text
        assert "copies the whole orders table" in text
        assert "Safer:" in text
        assert "deletes data  Drops column orders.legacy (line 18)" in text
        assert text.count("Safer:") == 1

        table.move_cursor(row=table.get_row_index("aaa"))
        await pilot.pause()
        assert details.display is False


async def test_no_mysql_column_for_a_sqlite_project():
    app = LazyAlembicApp(load_chain(PLAYGROUND), make_url("sqlite:///unused.db"))
    async with app.run_test():
        columns = [str(c.value) for c in app.query_one("#chain", DataTable).columns]
        assert "risk" not in columns
