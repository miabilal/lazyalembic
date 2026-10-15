"""Run the project's own migration commands (``flask db …`` or ``alembic …``)
in its folder and virtualenv."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lazyalembic.chain import Chain, find_ini

_VENV_NAMES = (".venv", "venv", "env")
_REVISION_LINE = re.compile(r"^\s*([0-9A-Za-z_]+)(?:\s+\(.*\))?\s*$")


class CommandError(Exception):
    """The project's migration command can't be run. Safe to show."""


@dataclass(frozen=True)
class Runner:
    """How to run migration commands for one project."""

    kind: str  # "flask" or "alembic"
    cwd: Path
    prefix: tuple[str, ...]

    def upgrade(self, revision: str) -> list[str]:
        return self._command("upgrade", revision)

    def downgrade(self, revision: str) -> list[str]:
        return self._command("downgrade", revision)

    def merge(self, message: str = "merge heads") -> list[str]:
        return self._command("merge", "-m", message, "heads")

    def current(self) -> list[str]:
        return self._command("current")

    def show(self, argv: list[str]) -> str:
        """The command as you'd type it in the project folder."""
        program = Path(argv[0])
        if program.parent.parent.name in _VENV_NAMES or program.parent.name == "bin":
            venv_parent = program.parent.parent.parent
            if self.cwd.is_relative_to(venv_parent):
                shown = os.path.relpath(program, self.cwd)
            else:
                shown = program.name
        else:
            shown = argv[0]
        return shlex.join([shown, *argv[1:]])

    def _command(self, *args: str) -> list[str]:
        if self.kind == "flask":
            # flask db <cmd> [-d dir] args: options go after the subcommand
            return [*self.prefix[:2], args[0], *self.prefix[2:], *args[1:]]
        return [*self.prefix, *args]


def find_runner(chain: Chain, start: Path) -> Runner:
    """Work out whether the project uses Flask-Migrate or plain Alembic."""
    script_dir = chain.versions_dirs[0].parent
    env_py = script_dir / "env.py"
    is_flask = env_py.is_file() and "current_app" in env_py.read_text(encoding="utf-8")

    if is_flask:
        cwd = script_dir.parent
        flask = _find_executable("flask", cwd)
        prefix = [flask, "db"]
        if script_dir.name != "migrations":
            prefix += ["-d", script_dir.name]
        return Runner("flask", cwd, tuple(prefix))

    ini = find_ini(start)
    if ini is None:
        raise CommandError(
            "This project has no alembic.ini, so lazyalembic can't run Alembic for it."
        )
    cwd = ini.parent
    return Runner("alembic", cwd, (_find_executable("alembic", cwd), "-c", ini.name))


@dataclass(frozen=True)
class Result:
    ok: bool
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)


def run(runner: Runner, argv: list[str]) -> Result:
    """Run a command in the project folder and wait for it to finish."""
    try:
        completed = subprocess.run(
            argv,
            cwd=runner.cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return Result(False, "", f"Couldn't start {argv[0]}: {exc.strerror}")
    return Result(completed.returncode == 0, completed.stdout, completed.stderr)


def parse_current(stdout: str) -> tuple[str, ...]:
    """Revision ids from `alembic current` / `flask db current` stdout.

    Anything else the project prints shows up as extra ids, which can only
    make the safety check fail, never pass.
    """
    heads = []
    for line in stdout.splitlines():
        if line.startswith(("INFO", "WARNING", "DEBUG")):
            continue
        match = _REVISION_LINE.match(line)
        if match:
            heads.append(match.group(1))
    return tuple(heads)


def _find_executable(name: str, cwd: Path) -> str:
    """``name`` from a virtualenv in or above ``cwd``, else from PATH."""
    home = Path.home()
    for folder in (cwd, *cwd.parents):
        for venv in _VENV_NAMES:
            candidate = folder / venv / "bin" / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        if folder == home:
            break
    found = shutil.which(name)
    if found is None:
        raise CommandError(
            f"Couldn't find `{name}`. Activate your project's virtualenv and "
            "start lazyalembic again."
        )
    return found
