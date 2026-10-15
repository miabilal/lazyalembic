"""Database changes through the screen, on a copy of the playground."""

import shutil
import subprocess

import pytest
from conftest import PLAYGROUND
from sqlalchemy.engine import make_url
from textual.widgets import DataTable, Static

from lazyalembic.app import LazyAlembicApp
from lazyalembic.chain import find_ini, load_chain
from lazyalembic.database import ENV_VAR, find_database_url, read_heads
from lazyalembic.screens import ConfirmScreen, ResultScreen


@pytest.fixture
def playground(tmp_path, monkeypatch):
    """Playground copy with its database on c3d5e4f6a7b8."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    copy = tmp_path / "playground"
    shutil.copytree(PLAYGROUND, copy, ignore=shutil.ignore_patterns("*.db", "__pycache__"))
    alembic(copy, "upgrade", "c3d5e4f6a7b8")
    return copy


def alembic(folder, *args):
    subprocess.run(
        [shutil.which("alembic"), "-c", "alembic.ini", *args],
        cwd=folder, check=True, capture_output=True,
    )


def db_heads(folder):
    return read_heads(find_database_url(find_ini(folder)))


def open_app(folder, database_url=None):
    url = database_url or find_database_url(find_ini(folder))
    return LazyAlembicApp(load_chain(folder), url, start=folder)


async def settle(app, pilot):
    # the change runs in one worker, then the refresh starts another
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()


def select(app, revision):
    table = app.query_one("#chain", DataTable)
    table.move_cursor(row=table.get_row_index(revision))


def text_of(screen, selector):
    return str(screen.query_one(selector, Static).render())


async def test_upgrade_one_branch(playground):
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "e5f7a6b8c9d0")
        await pilot.press("u")

        assert isinstance(app.screen, ConfirmScreen)
        assert "-c alembic.ini upgrade e5f7a6b8c9d0" in text_of(app.screen, "#command")
        await pilot.press("y")
        await settle(app, pilot)

        assert isinstance(app.screen, ResultScreen)
        assert "Done" in text_of(app.screen, "#title")
        await pilot.press("enter")
        await settle(app, pilot)

        table = app.query_one("#chain", DataTable)
        assert table.get_row("e5f7a6b8c9d0")[2].plain == "✓"
        assert table.get_row("d4e6f5a7b8c9")[2].plain == "·"
    assert db_heads(playground) == ("e5f7a6b8c9d0",)


async def test_cancel_changes_nothing(playground):
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "e5f7a6b8c9d0")
        await pilot.press("u")
        await pilot.press("n")
        await settle(app, pilot)
        assert not isinstance(app.screen, (ConfirmScreen, ResultScreen))
    assert db_heads(playground) == ("c3d5e4f6a7b8",)


async def test_downgrade_lists_what_it_undoes(playground):
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "a1f3c2d4e5b6")
        await pilot.press("d")

        assert isinstance(app.screen, ConfirmScreen)
        body = str(app.screen.body)
        assert "Undoes 2 migrations" in body
        assert "c3d5e4f6a7b8  Add gift wrap to orders" in body
        assert "b2e4d3c5f6a7  Create orders" in body
        await pilot.press("y")
        await settle(app, pilot)
        assert "Done" in text_of(app.screen, "#title")
    assert db_heads(playground) == ("a1f3c2d4e5b6",)


async def test_downgrade_with_both_branches_applied(playground):
    alembic(playground, "upgrade", "heads")
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "c3d5e4f6a7b8")
        await pilot.press("d")
        assert "Undoes 2 migrations" in str(app.screen.body)
        await pilot.press("y")
        await settle(app, pilot)
        assert "Done" in text_of(app.screen, "#title")
    assert db_heads(playground) == ("c3d5e4f6a7b8",)


async def test_join_ends_creates_a_merge_and_the_split_warning_goes(playground):
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert app.query_one("#split", Static).display is True
        await pilot.press("m")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)

        assert len(app.chain.heads) == 1
        assert app.chain.migrations[0].is_merge
        assert app.query_one("#split", Static).display is False
    merged = load_chain(playground).migrations[0]
    assert set(merged.down_revisions) == {"d4e6f5a7b8c9", "e5f7a6b8c9d0"}
    assert db_heads(playground) == ("c3d5e4f6a7b8",)


async def test_nothing_to_do_opens_no_confirm_box(playground):
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "b2e4d3c5f6a7")  # already applied
        await pilot.press("u")
        select(app, "d4e6f5a7b8c9")  # not applied, so can't go back to it
        await pilot.press("d")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)


async def test_database_on_another_computer_is_never_changed(playground):
    remote = make_url("postgresql://u:p@db.example.com/shop")
    app = open_app(playground, database_url=remote)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "e5f7a6b8c9d0")
        await pilot.press("u")
        await pilot.press("d")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)


async def test_stops_when_the_project_points_at_a_different_database(playground, tmp_path):
    # lazyalembic looks at other.db; the project's alembic.ini points at playground.db
    other = tmp_path / "other"
    shutil.copytree(playground, other)
    (other / "playground.db").unlink()
    alembic(other, "upgrade", "b2e4d3c5f6a7")
    other_url = make_url(f"sqlite:///{other / 'playground.db'}")

    app = open_app(playground, database_url=other_url)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "c3d5e4f6a7b8")
        await pilot.press("u")
        await pilot.press("y")
        await settle(app, pilot)

        assert isinstance(app.screen, ResultScreen)
        assert "nothing was changed" in text_of(app.screen, "#title")
        assert "different databases" in text_of(app.screen, "#message")
    assert db_heads(playground) == ("c3d5e4f6a7b8",)
    assert read_heads(other_url) == ("b2e4d3c5f6a7",)


async def test_upgrade_confirm_warns_about_mysql_risks(playground, monkeypatch):
    # playground is SQLite, so force the MySQL rules on
    monkeypatch.setattr("lazyalembic.app.rules_apply", lambda url, paths: True)
    (playground / "migrations" / "versions" / "f1a2b3c4d5e6_widen_email.py").write_text(
        '"""Make customer_email longer\n\n"""\n'
        "import sqlalchemy as sa\nfrom alembic import op\n\n"
        "revision = 'f1a2b3c4d5e6'\ndown_revision = 'e5f7a6b8c9d0'\n\n\n"
        "def upgrade():\n"
        "    op.alter_column('orders', 'customer_email', existing_type=sa.String(50), type_=sa.String(320))\n"
        "    op.create_index('ix_orders_email', 'orders', ['customer_email'])\n\n\n"
        "def downgrade():\n    pass\n"
    )
    app = open_app(playground)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        select(app, "f1a2b3c4d5e6")
        await pilot.press("u")
        body = str(app.screen.body)
        assert "⚠ On MySQL:" in body
        assert "blocks writes" in body
        assert "Changes the type of orders.customer_email  (f1a2b3c4d5e6)" in body
        # index builds aren't listed
        assert "ix_orders_email" not in body
        await pilot.press("n")
