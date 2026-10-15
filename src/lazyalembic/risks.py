"""Flag migration steps that are risky on MySQL.

Rules follow the MySQL 8.0 manual's online DDL tables and assume MySQL 8.0+
with utf8mb4.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL

# worst first
BLOCKS = "blocks writes"
DATA = "deletes data"
REBUILD = "rebuilds table"
CHECK = "check SQL"
SLOW = "slow on big tables"
LEVELS = (BLOCKS, DATA, REBUILD, CHECK, SLOW)

_BYTES_PER_CHAR = 4  # utf8mb4
_VARCHAR_TYPES = {"String", "VARCHAR", "Unicode", "NVARCHAR"}
_ENUM_TYPES = {"Enum", "ENUM"}
_MYSQL_MARKER = re.compile(r"sqlalchemy\.dialects\s*(?:import\s+mysql|\.mysql)|\bmysql_\w+\s*=")

_COPY_WHY = (
    "MySQL copies the whole {table} table to do this, and inserts and updates "
    "on it wait until the copy finishes."
)
_COPY_TIP = (
    "On a big table, run it with an online schema change tool (gh-ost or "
    "pt-online-schema-change), or add a new column, copy the data across in "
    "batches, and switch over."
)


@dataclass(frozen=True)
class Risk:
    level: str
    table: str
    line: int
    what: str
    why: str
    tip: str


def rules_apply(database_url: URL | None, migration_paths: list[Path]) -> bool:
    """True for a MySQL/MariaDB database, or with no database, when the
    migrations use MySQL features."""
    if database_url is not None:
        return database_url.get_backend_name() in ("mysql", "mariadb")
    for path in migration_paths:
        try:
            if _MYSQL_MARKER.search(path.read_text(encoding="utf-8")):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def find_risks(path: Path) -> list[Risk]:
    """Risky operations in one migration's upgrade(), worst first."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []  # load_chain already reports unreadable files
    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if upgrade is None:
        return []

    batches = _batch_tables(upgrade)
    created = _created_tables(upgrade)
    risks = []
    for node in ast.walk(upgrade):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        if not isinstance(receiver, ast.Name):
            continue
        if receiver.id == "op":
            batch_table = None
        elif receiver.id in batches:
            batch_table = batches[receiver.id]
        else:
            continue
        rule = _CHECKS.get(node.func.attr)
        if rule is not None:
            check, table_at, table_kw = rule
            risks.extend(check(_Call(node, batch_table, table_at, table_kw)))
    # tables created in this migration are empty
    risks = [r for r in risks if r.table not in created]
    return sorted(risks, key=lambda r: (LEVELS.index(r.level), r.line))



class _Call:
    """An op.x() or batch_op.x() call with the table argument taken out, so
    both forms line up."""

    def __init__(
        self, node: ast.Call, batch_table: str | None, table_at: int | None, table_kw: str
    ) -> None:
        self.line = node.lineno
        self.batch_table = batch_table
        args = list(node.args)
        self.kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        self.table_node = None
        if batch_table is None and table_at is not None:
            if table_at < len(args):
                self.table_node = args.pop(table_at)
            else:
                self.table_node = self.kwargs.get(table_kw)
        self.args = args

    @property
    def table(self) -> str:
        if self.batch_table is not None:
            return self.batch_table
        return _str(self.table_node) or "?"

    def arg(self, index: int, name: str) -> ast.expr | None:
        if name in self.kwargs:
            return self.kwargs[name]
        return self.args[index] if index < len(self.args) else None


def _created_tables(func: ast.FunctionDef) -> set[str]:
    return {
        name
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_table"
        and node.args
        and (name := _str(node.args[0]))
    }


def _batch_tables(func: ast.FunctionDef) -> dict[str, str]:
    """`with op.batch_alter_table("t") as b:` -> {"b": "t"}"""
    found = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "batch_alter_table"
                and isinstance(item.optional_vars, ast.Name)
            ):
                table = _str(call.args[0]) if call.args else None
                table = table or _str(next(
                    (k.value for k in call.keywords if k.arg == "table_name"), None
                ))
                found[item.optional_vars.id] = table or "?"
    return found


def _str(node: ast.expr | None) -> str | None:
    """A string literal, also seen through `op.f("name")` naming wrappers."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "f"
        and node.args
    ):
        node = node.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _sql(node: ast.expr | None) -> str | None:
    """The literal text of a SQL argument: "…", f"…" (fixed parts) or text("…")."""
    if isinstance(node, ast.Call) and node.args:
        return _sql(node.args[0])
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return _str(node)


def _call_name(node: ast.expr | None) -> str | None:
    """`sa.String(50)` -> "String", `mysql.VARCHAR(10)` -> "VARCHAR"."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False



def _varchar_length(node: ast.expr | None) -> int | None:
    if _call_name(node) not in _VARCHAR_TYPES or not isinstance(node, ast.Call):
        return None
    length = node.args[0] if node.args else next(
        (k.value for k in node.keywords if k.arg == "length"), None
    )
    if isinstance(length, ast.Constant) and isinstance(length.value, int):
        return length.value
    return None


def _enum_values(node: ast.expr | None) -> list[str] | None:
    if _call_name(node) not in _ENUM_TYPES or not isinstance(node, ast.Call):
        return None
    values = [_str(a) for a in node.args]
    return values if values and all(v is not None for v in values) else None


def _type_change_in_place(old: ast.expr | None, new: ast.expr | None) -> bool | None:
    """True if MySQL can change the type without copying the table, False if
    it must copy it, None if lazyalembic can't tell (no existing_type)."""
    new_len, old_len = _varchar_length(new), _varchar_length(old)
    if new_len is not None and old_len is not None:
        if new_len < old_len:
            return False  # shrinking a VARCHAR needs a table copy
        # growing stays in place unless it crosses 255 bytes (length prefix 1 -> 2 bytes)
        small = 255 // _BYTES_PER_CHAR
        return (old_len <= small) == (new_len <= small)

    new_values, old_values = _enum_values(new), _enum_values(old)
    if new_values is not None and old_values is not None:
        # appending members is instant while storage stays 1 byte
        return new_values[: len(old_values)] == old_values and len(new_values) <= 255

    if old is None:
        return None
    return False



def _alter_column(call: _Call) -> list[Risk]:
    column = _str(call.arg(0, "column_name")) or "?"
    name = f"{call.table}.{column}"
    not_null = _false(call.kwargs.get("nullable"))
    null_note = (
        " It also fails partway if any row already has NULL in this column."
        if not_null else ""
    )

    if "type_" in call.kwargs:
        in_place = _type_change_in_place(call.kwargs.get("existing_type"), call.kwargs["type_"])
        if in_place is not True:
            why = _COPY_WHY.format(table=call.table) + null_note
            if in_place is None:
                why += (
                    " (Only lengthening a VARCHAR within the same size band avoids "
                    "the copy; add existing_type= so lazyalembic can tell.)"
                )
            return [Risk(BLOCKS, call.table, call.line, f"Changes the type of {name}", why, _COPY_TIP)]

    if "nullable" in call.kwargs:
        becomes = "required (NOT NULL)" if not_null else "optional (NULL)"
        return [Risk(
            REBUILD, call.table, call.line, f"Makes {name} {becomes}",
            f"MySQL rebuilds the {call.table} table. Writes keep working, but on a "
            f"big table it takes a long time and needs free disk space about the "
            f"size of the table.{null_note}",
            "Fill in any NULLs first, and run it at a quiet time on a big table."
            if not_null else "Run it at a quiet time on a big table.",
        )]
    return []


def _add_column(call: _Call) -> list[Risk]:
    column = call.arg(0, "column")
    if not isinstance(column, ast.Call):
        return []
    name = f"{call.table}.{_str(column.args[0]) if column.args else '?'}"
    kwargs = {k.arg: k.value for k in column.keywords if k.arg}
    parts = column.args[1:]
    risks = []

    computed = next((p for p in parts if _call_name(p) == "Computed"), None)
    if computed is not None and _true(
        next((k.value for k in computed.keywords if k.arg == "persisted"), None)
    ):
        risks.append(Risk(
            BLOCKS, call.table, call.line, f"Adds stored generated column {name}",
            _COPY_WHY.format(table=call.table) + " MySQL has to work out the value for every row.",
            "Use a VIRTUAL generated column if it doesn't need to be stored, or run "
            "it at a quiet time.",
        ))
    if _true(kwargs.get("autoincrement")):
        risks.append(Risk(
            BLOCKS, call.table, call.line, f"Adds auto-increment column {name}",
            f"MySQL rebuilds the {call.table} table and doesn't allow writes until it finishes.",
            "Run it at a quiet time on a big table.",
        ))
    if any(_call_name(p) == "ForeignKey" for p in parts):
        risks.append(_foreign_key_risk(call, f"Adds {name} with a foreign key"))
    if _true(kwargs.get("index")) or _true(kwargs.get("unique")):
        risks.append(_index_risk(call, f"Adds an index on {name}", _true(kwargs.get("unique"))))
    return risks


def _create_index(call: _Call) -> list[Risk]:
    name = _str(call.arg(0, "index_name"))
    prefix = (_str(call.kwargs.get("mysql_prefix")) or "").upper()
    if prefix in ("FULLTEXT", "SPATIAL"):
        return [Risk(
            BLOCKS, call.table, call.line, f"Adds {_named(f'{prefix} index', name)} on {call.table}",
            f"Writes to {call.table} wait while MySQL builds a {prefix} index.",
            "Run it at a quiet time on a big table.",
        )]
    return [_index_risk(
        call, f"Adds {_named('index', name)} on {call.table}", _true(call.kwargs.get("unique"))
    )]


def _create_unique_constraint(call: _Call) -> list[Risk]:
    name = _str(call.arg(0, "constraint_name"))
    return [_index_risk(
        call, f"Adds {_named('unique constraint', name)} on {call.table}", unique=True
    )]


def _create_foreign_key(call: _Call) -> list[Risk]:
    name = _str(call.arg(0, "constraint_name"))
    return [_foreign_key_risk(call, f"Adds {_named('foreign key', name)} on {call.table}")]


def _create_primary_key(call: _Call) -> list[Risk]:
    return [Risk(
        REBUILD, call.table, call.line, f"Adds a primary key to {call.table}",
        f"MySQL rebuilds and reorganises the whole {call.table} table. Writes keep "
        "working, but it is one of the most expensive changes.",
        "Run it at a quiet time on a big table.",
    )]


def _drop_constraint(call: _Call) -> list[Risk]:
    if (_str(call.arg(1, "type_")) or "") != "primary":
        return []
    return [Risk(
        BLOCKS, call.table, call.line, f"Drops the primary key of {call.table}",
        _COPY_WHY.format(table=call.table),
        "Drop and add the new primary key in the same migration step, which "
        "MySQL can do without blocking writes.",
    )]


def _drop_column(call: _Call) -> list[Risk]:
    column = _str(call.arg(0, "column_name")) or "?"
    return [Risk(
        DATA, call.table, call.line, f"Drops column {call.table}.{column}",
        "The data in this column is gone; downgrading brings the column back empty. "
        "Code still reading it breaks the moment it runs.",
        "Ship code that no longer uses the column first, drop it in a later "
        "release, and back it up if the data matters.",
    )]


def _drop_table(call: _Call) -> list[Risk]:
    return [Risk(
        DATA, call.table, call.line, f"Drops table {call.table}",
        "Every row in it is gone; downgrading brings the table back empty.",
        "Back it up first if the data matters.",
    )]


def _execute(call: _Call) -> list[Risk]:
    sql = _sql(call.arg(0, "sqltext"))
    if not sql:
        return []
    upper = " ".join(sql.upper().split())
    table = _table_in_sql(sql) or "?"
    if "CONVERT TO CHARACTER SET" in upper:
        return [Risk(
            BLOCKS, table, call.line, f"Converts the character set of {table}",
            f"MySQL rebuilds {table} and writes to it wait until it finishes.",
            "Run it at a quiet time, or with an online schema change tool on a big table.",
        )]
    if "ALTER TABLE" in upper:
        return [Risk(
            CHECK, table, call.line, f"Runs raw ALTER TABLE on {table}",
            "lazyalembic can't tell how MySQL will run hand-written SQL; some ALTERs "
            "copy the whole table and block writes.",
            "Check it against MySQL's online DDL table, or add ALGORITHM=INPLACE, "
            "LOCK=NONE so MySQL refuses instead of blocking.",
        )]
    if upper.lstrip("( ").startswith(("UPDATE", "DELETE")):
        verb = "Updates" if upper.lstrip("( ").startswith("UPDATE") else "Deletes"
        return [Risk(
            SLOW, table, call.line, f"{verb} rows in {table} in one statement",
            "Every matching row is locked until the statement finishes; on a big "
            "table other writes to those rows wait.",
            "On a big table, do it in batches (for example 1,000 rows at a time).",
        )]
    return []


def _foreign_key_risk(call: _Call, what: str) -> Risk:
    return Risk(
        BLOCKS, call.table, call.line, what,
        _COPY_WHY.format(table=call.table)
        + " (With foreign_key_checks on, the MySQL default, adding a foreign key "
        "is a table copy.)",
        "Run it at a quiet time. MySQL can add it without copying when "
        "foreign_key_checks is off for that session, but then existing rows aren't "
        "checked; only do that if you're sure the data is valid.",
    )


def _index_risk(call: _Call, what: str, unique: bool) -> Risk:
    fails = " It fails if existing rows have duplicate values." if unique else ""
    return Risk(
        SLOW, call.table, call.line, what,
        "Writes keep working while MySQL builds the index, but on a big table it "
        f"takes a while.{fails}",
        "Fine for most tables; run it at a quiet time on a very big one.",
    )


def _named(kind: str, name: str | None) -> str:
    """'index ix_a' when named, 'an index' when not."""
    if name:
        return f"{kind} {name}"
    return f"{'an' if kind[0].lower() in 'aeiou' else 'a'} {kind}"


def _table_in_sql(sql: str) -> str | None:
    match = re.search(
        r"\b(?:ALTER\s+TABLE|UPDATE|DELETE\s+FROM)\s+`?([\w.]+)`?", sql, re.IGNORECASE
    )
    return match.group(1) if match else None


# op name -> (check, table arg position in op.x(), table keyword)
_CHECKS = {
    "alter_column": (_alter_column, 0, "table_name"),
    "add_column": (_add_column, 0, "table_name"),
    "drop_column": (_drop_column, 0, "table_name"),
    "drop_table": (_drop_table, 0, "table_name"),
    "create_index": (_create_index, 1, "table_name"),
    "create_unique_constraint": (_create_unique_constraint, 1, "table_name"),
    "create_foreign_key": (_create_foreign_key, 1, "source_table"),
    "create_primary_key": (_create_primary_key, 1, "table_name"),
    "drop_constraint": (_drop_constraint, 1, "table_name"),
    "execute": (_execute, None, ""),
}
