import pytest
from conftest import PLAYGROUND, write_migration

from lazyalembic import commands
from lazyalembic.chain import load_chain
from lazyalembic.commands import CommandError, find_runner, parse_current


def flask_project(tmp_path, folder="migrations", venv=True):
    script_dir = tmp_path / folder
    (script_dir / "versions").mkdir(parents=True)
    (script_dir / "env.py").write_text("from flask import current_app\n")
    (script_dir / "versions" / "aaa_change.py").write_text("revision = 'aaa'\ndown_revision = None\n")
    if venv:
        flask = tmp_path / "venv" / "bin" / "flask"
        flask.parent.mkdir(parents=True)
        flask.write_text("#!/bin/sh\n")
        flask.chmod(0o755)
    return tmp_path


def test_plain_alembic_project_runs_alembic_next_to_its_ini():
    runner = find_runner(load_chain(PLAYGROUND), PLAYGROUND)
    assert runner.kind == "alembic"
    assert runner.cwd == PLAYGROUND
    argv = runner.upgrade("e5f7a6b8c9d0")
    assert argv[1:] == ["-c", "alembic.ini", "upgrade", "e5f7a6b8c9d0"]
    assert runner.show(argv).endswith("alembic -c alembic.ini upgrade e5f7a6b8c9d0")


def test_flask_migrate_project_uses_its_own_venv_flask(tmp_path):
    project = flask_project(tmp_path)
    runner = find_runner(load_chain(project), project)
    assert runner.kind == "flask"
    assert runner.cwd == project

    argv = runner.upgrade("aaa")
    assert argv == [str(project / "venv" / "bin" / "flask"), "db", "upgrade", "aaa"]
    assert runner.show(argv) == "venv/bin/flask db upgrade aaa"
    assert runner.show(runner.merge()) == "venv/bin/flask db merge -m 'merge heads' heads"
    assert runner.show(runner.current()) == "venv/bin/flask db current"


def test_flask_migrate_with_a_custom_folder_passes_it(tmp_path):
    project = flask_project(tmp_path, folder="db_migrations")
    runner = find_runner(load_chain(project / "db_migrations"), project / "db_migrations")
    assert runner.show(runner.downgrade("aaa")) == "venv/bin/flask db downgrade -d db_migrations aaa"


def test_missing_program_is_explained(tmp_path, monkeypatch):
    project = flask_project(tmp_path, venv=False)
    monkeypatch.setattr(commands.shutil, "which", lambda name: None)
    monkeypatch.setattr(commands.Path, "home", lambda: tmp_path)
    with pytest.raises(CommandError, match="Couldn't find `flask`"):
        find_runner(load_chain(project), project)


def test_plain_project_without_ini_cannot_run_alembic(project):
    write_migration(project, "aaa", None)
    with pytest.raises(CommandError, match="no alembic.ini"):
        find_runner(load_chain(project), project)


def test_program_on_path_is_shown_by_name(tmp_path, monkeypatch):
    project = flask_project(tmp_path, venv=False)
    monkeypatch.setattr(commands.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(commands.shutil, "which", lambda name: f"/opt/tools/bin/{name}")
    runner = find_runner(load_chain(project), project)
    assert runner.show(runner.current()) == "flask db current"


@pytest.mark.parametrize(
    ("stdout", "heads"),
    [
        ("c3d5e4f6a7b8 (head)\n", ("c3d5e4f6a7b8",)),
        ("e5f7a6b8c9d0 (head)\nd4e6f5a7b8c9 (head)\n", ("e5f7a6b8c9d0", "d4e6f5a7b8c9")),
        ("b2e4d3c5f6a7\n", ("b2e4d3c5f6a7",)),
        ("c3d5e4f6a7b8 (branchpoint)\n", ("c3d5e4f6a7b8",)),
        ("INFO  [alembic.runtime.migration] Context impl SQLiteImpl.\n", ()),
        ("", ()),
    ],
)
def test_parse_current(stdout, heads):
    assert parse_current(stdout) == heads


def test_run_reports_a_program_that_cannot_start(tmp_path):
    runner = commands.Runner("alembic", tmp_path, ("/nonexistent/alembic",))
    result = commands.run(runner, ["/nonexistent/alembic", "current"])
    assert not result.ok
    assert "Couldn't start" in result.output


def test_run_captures_output_and_exit_code(tmp_path):
    runner = commands.Runner("alembic", tmp_path, ())
    ok = commands.run(runner, ["/bin/sh", "-c", "echo hello; echo oops >&2"])
    assert ok.ok and ok.stdout.strip() == "hello" and "oops" in ok.output
    failed = commands.run(runner, ["/bin/sh", "-c", "exit 3"])
    assert not failed.ok
    assert failed.output == ""
