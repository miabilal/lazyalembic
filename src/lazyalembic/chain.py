"""Read an Alembic migration chain without running any migration code.

Files are parsed with ``ast``; Alembic's ``RevisionMap`` works out the order,
heads, branch points and merges.
"""

from __future__ import annotations

import ast
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from alembic.config import Config
from alembic.script.revision import Revision, RevisionError, RevisionMap

# same rule as alembic.script.base._only_source_rev_file
_MIGRATION_FILE = re.compile(r"(?!\.\#|__init__)(.*\.py)$")
_CREATE_DATE = re.compile(r"^Create Date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_FIELDS = ("revision", "down_revision", "branch_labels", "depends_on")


class ChainError(Exception):
    """The migrations could not be found or don't form a usable chain."""


@dataclass(frozen=True)
class Migration:
    revision: str
    down_revisions: tuple[str, ...]
    message: str
    created: str | None
    path: Path
    is_head: bool
    is_base: bool
    is_branch_point: bool
    is_merge: bool


@dataclass
class Chain:
    project_name: str
    versions_dirs: list[Path]
    # newest first, like `alembic history`
    migrations: list[Migration]
    heads: tuple[str, ...]
    # unreadable files and chain warnings; shown, not fatal
    problems: list[str] = field(default_factory=list)


def load_chain(start: Path) -> Chain:
    """Load the migration chain for the project at ``start``.

    ``start`` may be a project folder, a migrations folder, or an
    ``alembic.ini`` file.
    """
    versions_dirs, recursive = find_versions_dirs(start)

    parsed: list[_Parsed] = []
    problems: list[str] = []
    for path in _migration_files(versions_dirs, recursive):
        try:
            migration = _read_migration(path)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            problems.append(f"Couldn't read {path.name}: {_reason(exc)}")
            continue
        if migration is not None:
            parsed.append(migration)

    if not parsed:
        where = ", ".join(str(d) for d in versions_dirs)
        raise ChainError(f"No migrations found in {where}.")

    # Alembic raises on a parent that isn't on disk (deleted, or a branch
    # not pulled yet); report it and treat the migration as a base instead
    known = {p.revision for p in parsed}
    revisions: list[Revision] = []
    details: dict[str, _Parsed] = {}
    for p in parsed:
        missing = [d for d in p.down_revisions if d not in known]
        if missing:
            problems.append(
                f"{p.revision} builds on {', '.join(missing)}, which isn't in the "
                "migrations folder (deleted, or not pulled yet?)"
            )
        try:
            revisions.append(
                Revision(
                    p.revision,
                    tuple(d for d in p.down_revisions if d in known) or None,
                    dependencies=p.depends_on or None,
                    branch_labels=p.branch_labels or None,
                )
            )
        except RevisionError as exc:
            problems.append(f"Couldn't read {p.path.name}: {exc}")
            continue
        details.setdefault(p.revision, p)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        revision_map = RevisionMap(lambda: revisions)
        try:
            ordered = list(
                revision_map.iterate_revisions(
                    "heads", "base", inclusive=True, assert_relative_length=False
                )
            )
            heads = revision_map.heads
        except RevisionError as exc:
            raise ChainError(f"The migration chain is broken: {exc}") from exc
        except KeyError as exc:
            raise ChainError(
                f"A migration depends on {exc.args[0]}, which isn't in the migrations folder."
            ) from exc
    problems.extend(str(w.message) for w in caught)

    migrations = []
    for rev in ordered:
        p = details[rev.revision]
        migrations.append(
            Migration(
                revision=rev.revision,
                down_revisions=p.down_revisions,
                message=p.message,
                created=p.created,
                path=p.path,
                is_head=rev.is_head,
                is_base=rev.is_base,
                is_branch_point=rev.is_branch_point,
                is_merge=rev.is_merge_point,
            )
        )

    return Chain(
        project_name=versions_dirs[0].parent.parent.name,
        versions_dirs=versions_dirs,
        migrations=migrations,
        heads=tuple(heads),
        problems=problems,
    )


def ancestors(chain: Chain, revision: str) -> set[str]:
    """``revision`` and every migration it builds on."""
    parents = {m.revision: m.down_revisions for m in chain.migrations}
    found: set[str] = set()
    todo = [revision]
    while todo:
        rev = todo.pop()
        if rev in found or rev not in parents:
            continue
        found.add(rev)
        todo.extend(parents[rev])
    return found


def find_ini(start: Path) -> Path | None:
    """The project's alembic.ini: ``start`` itself, ``start/alembic.ini`` or
    ``start/migrations/alembic.ini`` (Flask-Migrate)."""
    start = start.expanduser().resolve()
    if start.is_file():
        return start
    for candidate in (start / "alembic.ini", start / "migrations" / "alembic.ini"):
        if candidate.is_file():
            return candidate
    return None


def find_versions_dirs(start: Path) -> tuple[list[Path], bool]:
    """Folders holding migration files, and whether to search them recursively.

    Falls back to looking on disk when there's no alembic.ini or it has no
    ``script_location`` (Flask-Migrate).
    """
    start = start.expanduser().resolve()
    if not start.exists():
        raise ChainError(f"{start} does not exist.")

    ini = find_ini(start)
    if ini is not None:
        found = _versions_dirs_from_ini(ini)
        if found:
            return found

    base = start.parent if start.is_file() else start
    for candidate in (base, base / "migrations", base / "alembic"):
        if (candidate / "env.py").is_file() and (candidate / "versions").is_dir():
            return [candidate / "versions"], False

    raise ChainError(
        f"No Alembic migrations found in {base}. Run lazyalembic inside your "
        "project, or pass the path to its alembic.ini."
    )


def _versions_dirs_from_ini(ini: Path) -> tuple[list[Path], bool] | None:
    config = Config(str(ini))
    location = config.get_alembic_option("script_location")
    if not location:
        return None

    here = ini.parent
    version_locations = config.get_version_locations_list()
    if version_locations:
        dirs = [_resolve(here, loc) for loc in version_locations]
    else:
        dirs = [_resolve(here, location) / "versions"]

    dirs = [d for d in dirs if d.is_dir()]
    if not dirs:
        return None
    recursive = bool(config.get_alembic_boolean_option("recursive_version_locations"))
    return dirs, recursive


def _resolve(here: Path, location: str) -> Path:
    # "package:path" locations aren't supported (they'd need project code imported)
    path = Path(location).expanduser()
    return path if path.is_absolute() else (here / path).resolve()


def _migration_files(dirs: list[Path], recursive: bool) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for top in dirs:
        for root, subdirs, names in os.walk(top):
            subdirs[:] = sorted(d for d in subdirs if d != "__pycache__") if recursive else []
            for name in sorted(names):
                path = (Path(root) / name).resolve()
                if path in seen or not path.is_file() or not _MIGRATION_FILE.match(name):
                    continue
                seen.add(path)
                files.append(path)
    return files


@dataclass(frozen=True)
class _Parsed:
    revision: str
    down_revisions: tuple[str, ...]
    depends_on: tuple[str, ...]
    branch_labels: tuple[str, ...]
    message: str
    created: str | None
    path: Path


def _read_migration(path: Path) -> _Parsed | None:
    """Parse one migration file, or return None if it isn't a migration."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id in _FIELDS:
            try:
                values[target.id] = ast.literal_eval(value)
            except ValueError:
                raise ValueError(
                    f"{target.id} is not a plain value (line {node.lineno})"
                ) from None

    if "revision" not in values:
        return None
    if not isinstance(values["revision"], str):
        raise ValueError("revision is not a string")

    docstring = ast.get_docstring(tree) or ""
    created = _CREATE_DATE.search(docstring)
    return _Parsed(
        revision=values["revision"],
        down_revisions=_as_ids(values.get("down_revision")),
        depends_on=_as_ids(values.get("depends_on")),
        branch_labels=_as_ids(values.get("branch_labels")),
        # Alembic's message is the docstring's first paragraph
        message=" ".join(docstring.split("\n\n", 1)[0].split()),
        created=created.group(1) if created else None,
        path=path,
    )


def _as_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ValueError(f"expected a revision id or a list of ids, got {value!r}")


def _reason(exc: Exception) -> str:
    if isinstance(exc, SyntaxError):
        return f"syntax error on line {exc.lineno}"
    if isinstance(exc, UnicodeDecodeError):
        return "not a UTF-8 text file"
    return str(exc)
