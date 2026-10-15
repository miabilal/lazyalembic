"""The main lazyalembic screen."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from sqlalchemy.engine import URL
from textual import work
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from lazyalembic.chain import Chain, ChainError, Migration, ancestors, load_chain
from lazyalembic.commands import CommandError, Runner, find_runner, parse_current, run
from lazyalembic.database import (
    ENV_VAR,
    DatabaseError,
    DatabaseState,
    compare,
    describe,
    is_local,
    read_heads,
)
from lazyalembic.graph import draw
from lazyalembic.risks import (
    BLOCKS,
    CHECK,
    DATA,
    REBUILD,
    SLOW,
    Risk,
    find_risks,
    rules_apply,
)
from lazyalembic.screens import ConfirmScreen, ResultScreen

LANE_COLORS = ["cyan", "magenta", "yellow", "green", "blue", "red"]
LIST_LIMIT = 6
DETAIL_LIMIT = 4
RISK_LABELS = {
    BLOCKS: ("blocks writes", "bold red"),
    DATA: ("deletes data", "bold red"),
    REBUILD: ("rebuilds", "yellow"),
    CHECK: ("check SQL", "yellow"),
    SLOW: ("slow", "dim"),
}


class LazyAlembicApp(App):
    """Terminal map of an Alembic migration chain."""

    TITLE = "lazyalembic"

    CSS = """
    #database {
        height: auto;
        padding: 0 1;
    }
    #split {
        height: auto;
        padding: 0 1;
        color: $warning;
        border-bottom: solid $warning;
    }
    #chain {
        height: 1fr;
    }
    #details {
        height: auto;
        max-height: 14;
        padding: 0 1;
        border-top: solid $secondary;
    }
    #problems {
        height: auto;
        max-height: 8;
        padding: 0 1;
        color: $warning;
        border-top: solid $warning;
    }
    """

    BINDINGS = [
        ("u", "upgrade", "Upgrade to here"),
        ("d", "downgrade", "Downgrade to here"),
        ("m", "merge", "Join ends"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self, chain: Chain, database_url: URL | None = None, start: Path | None = None
    ) -> None:
        super().__init__()
        self.chain = chain
        self.database_url = database_url
        self.start = start
        self.db_state: DatabaseState | None = None
        self.busy = False
        self.risks: dict[str, list[Risk]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="database")
        yield Static(id="split")
        yield DataTable(id="chain", cursor_type="row")
        yield Static(id="details")
        yield Static(id="problems")
        yield Footer()

    def on_mount(self) -> None:
        self._populate()
        self._start_database_read()


    def _populate(self) -> None:
        chain = self.chain
        count, heads = len(chain.migrations), len(chain.heads)
        self.sub_title = (
            f"{chain.project_name} · {count} migration{'s' if count != 1 else ''}"
            f" · {heads} head{'s' if heads != 1 else ''}"
        )

        split = self.query_one("#split", Static)
        split.display = heads > 1
        if heads > 1:
            split.update(_split_warning(chain))

        paths = [m.path for m in chain.migrations]
        check_risks = rules_apply(self.database_url, paths)
        self.risks = {m.revision: find_risks(m.path) for m in chain.migrations} if check_risks else {}

        table = self.query_one("#chain", DataTable)
        selected = _selected_key(table)
        table.clear(columns=True)
        table.add_column("", key="graph")
        table.add_column("", key="tags")
        if self.database_url is not None:
            table.add_column("DB", key="db")
        if check_risks:
            table.add_column("MySQL risk", key="risk")
        table.add_column("Revision", key="revision")
        table.add_column("Date", key="date")
        # message last so a long one doesn't push the date off screen
        table.add_column("Message", key="message")
        for migration, line in zip(chain.migrations, draw(chain.migrations)):
            cells = [_colored(line), _tags(migration)]
            if self.database_url is not None:
                cells.append("")
            if check_risks:
                cells.append(_risk_cell(self.risks[migration.revision]))
            cells += [migration.revision, migration.created or "", migration.message]
            table.add_row(*cells, key=migration.revision)
        if selected is not None and selected in {m.revision for m in chain.migrations}:
            table.move_cursor(row=table.get_row_index(selected))
        self._show_details(_selected_key(table))

        problems = self.query_one("#problems", Static)
        problems.display = bool(chain.problems)
        if chain.problems:
            problems.update(Text("\n".join(f"⚠ {p}" for p in chain.problems)))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_details(event.row_key.value)

    def _show_details(self, revision: str | None) -> None:
        details = self.query_one("#details", Static)
        risks = self.risks.get(revision or "", [])
        details.display = bool(risks)
        if not risks:
            return
        message = next(m.message for m in self.chain.migrations if m.revision == revision)
        text = Text(f"{revision} · {message}", style="bold")
        worst, *rest = risks
        label, style = RISK_LABELS[worst.level]
        text.append(f"\n● {label}", style=style)
        text.append(f"  {worst.what} (line {worst.line})")
        text.append(f"\n    {worst.why}", style="dim")
        text.append(f"\n    Safer: {worst.tip}", style="green")
        for risk in rest[:DETAIL_LIMIT]:
            label, style = RISK_LABELS[risk.level]
            text.append(f"\n● {label}", style=style)
            text.append(f"  {risk.what} (line {risk.line})")
        if len(rest) > DETAIL_LIMIT:
            text.append(
                f"\n…and {len(rest) - DETAIL_LIMIT} more in "
                f"{_path_of(self.chain, revision).name}",
                style="dim",
            )
        text.append("\nRules assume MySQL 8.0+ with utf8mb4.", style="dim italic")
        details.update(text)

    def _start_database_read(self) -> None:
        database = self.query_one("#database", Static)
        self.db_state = None
        if self.database_url is None:
            database.update(Text(
                f"No database set. Pass --db URL or set ${ENV_VAR} to see which "
                "migrations are applied.",
                style="dim",
            ))
            return
        database.update(f"Database {describe(self.database_url)}: checking…")
        self.read_database()

    @work(thread=True, exclusive=True, group="database")
    def read_database(self) -> None:
        try:
            heads = read_heads(self.database_url)
        except DatabaseError as exc:
            self.call_from_thread(self._show_database_error, str(exc))
            return
        self.call_from_thread(self._show_database, compare(self.chain, heads))

    def _show_database(self, state: DatabaseState) -> None:
        self.db_state = state
        table = self.query_one("#chain", DataTable)
        for migration in self.chain.migrations:
            mark = (
                Text("✓", style="green") if migration.revision in state.applied
                else Text("·", style="dim")
            )
            table.update_cell(migration.revision, "db", mark)
        self.query_one("#database", Static).update(
            _database_summary(describe(self.database_url), state)
        )

    def _show_database_error(self, message: str) -> None:
        self.query_one("#database", Static).update(Text(f"⚠ {message}", style="red"))


    def action_refresh(self) -> None:
        try:
            self.chain = load_chain(self.start or Path("."))
        except ChainError as exc:
            self.notify(str(exc), severity="error")
            return
        self._populate()
        self._start_database_read()

    def action_upgrade(self) -> None:
        migration, runner = self._ready_to_change()
        if migration is None:
            return
        to_apply = ancestors(self.chain, migration.revision) - self.db_state.applied
        if not to_apply:
            self.notify("The database already has this migration.")
            return
        body = Text(
            f"Applies {_count(to_apply)} to {describe(self.database_url)}:\n"
        )
        body.append(self._listing(to_apply))
        body.append(self._risk_listing(to_apply))
        body.append(
            "\n\nFirst checks that your project's own settings point at this "
            "same database.",
            style="dim",
        )
        self._confirm(
            "Upgrade the database", body, runner, runner.upgrade(migration.revision),
            check_database=True,
        )

    def action_downgrade(self) -> None:
        migration, runner = self._ready_to_change()
        if migration is None:
            return
        if migration.revision not in self.db_state.applied:
            self.notify(
                "The database doesn't have this migration yet. "
                "Pick one with ✓ to go back to it."
            )
            return
        to_undo = self.db_state.applied - ancestors(self.chain, migration.revision)
        if not to_undo:
            self.notify("The database is already at this migration.")
            return
        body = Text(
            f"Undoes {_count(to_undo)} on {describe(self.database_url)}:\n"
        )
        body.append(self._listing(to_undo))
        body.append(
            "\n\nUndoing can delete data the migrations added (dropped columns "
            "lose their values).",
            style="yellow",
        )
        body.append(
            "\nFirst checks that your project's own settings point at this "
            "same database.",
            style="dim",
        )
        self._confirm(
            "Downgrade the database", body, runner, runner.downgrade(migration.revision),
            check_database=True,
        )

    def action_merge(self) -> None:
        if self.busy:
            self.notify("Still running the last command.")
            return
        if len(self.chain.heads) < 2:
            self.notify("The chain has one end; there is nothing to join.")
            return
        runner = self._runner()
        if runner is None:
            return
        body = Text("Creates a new migration file that joins:\n")
        body.append(self._listing(set(self.chain.heads)))
        body.append(
            "\n\nIt doesn't change any database. Apply it afterwards with u.",
            style="dim",
        )
        self._confirm(
            f"Join the {len(self.chain.heads)} ends", body, runner, runner.merge(),
            check_database=False,
        )


    def _ready_to_change(self) -> tuple[Migration | None, Runner | None]:
        """Selected migration and runner, or (None, None) after saying why not."""
        if self.busy:
            self.notify("Still running the last command.")
            return None, None
        if self.database_url is None:
            self.notify(
                f"Set the database first (--db URL or ${ENV_VAR}), so lazyalembic "
                "can check it's the one on your laptop.",
                severity="warning",
            )
            return None, None
        if not is_local(self.database_url):
            self.notify(
                f"lazyalembic only changes databases on this computer; "
                f"{describe(self.database_url)} isn't one.",
                severity="warning",
            )
            return None, None
        if self.db_state is None:
            self.notify("The database hasn't been read yet.", severity="warning")
            return None, None
        if self.db_state.unknown:
            self.notify(
                "The database is on a migration that isn't in this folder; "
                "sort that out before changing it.",
                severity="warning",
            )
            return None, None
        key = _selected_key(self.query_one("#chain", DataTable))
        migration = next((m for m in self.chain.migrations if m.revision == key), None)
        if migration is None:
            return None, None
        runner = self._runner()
        if runner is None:
            return None, None
        return migration, runner

    def _runner(self) -> Runner | None:
        try:
            return find_runner(self.chain, self.start or Path("."))
        except CommandError as exc:
            self.notify(str(exc), severity="error")
            return None

    def _confirm(
        self, title: str, body: Text, runner: Runner, argv: list[str], check_database: bool
    ) -> None:
        def answered(yes: bool | None) -> None:
            if yes:
                self.busy = True
                self.query_one("#database", Static).update(
                    Text(f"Running {runner.show(argv)} …", style="bold")
                )
                self.run_change(runner, argv, check_database)

        self.push_screen(
            ConfirmScreen(title, body, runner.show(argv), _short_path(runner.cwd)), answered
        )

    @work(thread=True, exclusive=True, group="change")
    def run_change(self, runner: Runner, argv: list[str], check_database: bool) -> None:
        shown = runner.show(argv)
        if check_database:
            # the project's command uses its own DB settings, so check it
            # sees the same database we're showing
            check = run(runner, runner.current())
            if not check.ok:
                self.call_from_thread(
                    self._finish, "Stopped: nothing was changed", runner.show(runner.current()),
                    check.output,
                    "Couldn't ask your project which migration its database is on.",
                )
                return
            project_heads = set(parse_current(check.stdout))
            ours = set(self.db_state.heads)
            if project_heads != ours:
                self.call_from_thread(
                    self._finish, "Stopped: nothing was changed", shown, check.output,
                    f"Your project's database is on {_ids(project_heads)}, but "
                    f"{describe(self.database_url)} is on {_ids(ours)}. They look "
                    "like different databases. Check your project's database "
                    "settings point at your laptop.",
                )
                return

        result = run(runner, argv)
        title = "✓ Done" if result.ok else "✗ Failed"
        self.call_from_thread(self._finish, title, shown, result.output, "")

    def _finish(self, title: str, command: str, output: str, message: str) -> None:
        self.busy = False
        self.push_screen(ResultScreen(title, command, output, message))
        self.action_refresh()

    def _risk_listing(self, revisions: set[str]) -> Text:
        """Serious MySQL risks among ``revisions`` (index builds left out)."""
        ordered = [m for m in self.chain.migrations if m.revision in revisions]
        risks = [
            (m.revision, r) for m in ordered for r in self.risks.get(m.revision, [])
            if r.level != SLOW
        ]
        if not risks:
            return Text()
        text = Text("\n\n⚠ On MySQL:", style="bold yellow")
        for revision, risk in risks[:LIST_LIMIT]:
            label, style = RISK_LABELS[risk.level]
            text.append(f"\n    {label:<13}", style=style)
            text.append(f" {risk.what}  ({revision})")
        if len(risks) > LIST_LIMIT:
            text.append(f"\n    …and {len(risks) - LIST_LIMIT} more", style="dim")
        return text

    def _listing(self, revisions: set[str]) -> Text:
        ordered = [m for m in self.chain.migrations if m.revision in revisions]
        text = Text()
        for m in ordered[:LIST_LIMIT]:
            text.append(f"\n    {m.revision}  {m.message}")
        if len(ordered) > LIST_LIMIT:
            text.append(f"\n    …and {len(ordered) - LIST_LIMIT} more")
        return text


def _risk_cell(risks: list[Risk]) -> Text:
    if not risks:
        return Text("")
    label, style = RISK_LABELS[risks[0].level]
    cell = Text(label, style=style)
    if len(risks) > 1:
        cell.append(f" +{len(risks) - 1}", style="dim")
    return cell


def _path_of(chain: Chain, revision: str) -> Path:
    return next(m.path for m in chain.migrations if m.revision == revision)


def _selected_key(table: DataTable) -> str | None:
    if table.row_count == 0:
        return None
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value


def _short_path(path: Path) -> str:
    home = Path.home()
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


def _count(revisions: set[str]) -> str:
    return f"{len(revisions)} migration{'s' if len(revisions) != 1 else ''}"


def _ids(revisions: set[str]) -> str:
    return ", ".join(sorted(revisions)) or "nothing"


def _database_summary(label: str, state: DatabaseState) -> Text:
    if state.unknown:
        unknown = ", ".join(state.unknown)
        return Text(
            f"⚠ Database {label} is on {unknown}, which isn't in the migrations "
            "folder (applied from another branch?)",
            style="yellow",
        )
    if not state.heads:
        return Text(f"Database {label}: nothing applied yet", style="yellow")
    on = ", ".join(state.heads)
    if state.pending:
        count = len(state.pending)
        return Text(
            f"Database {label} is on {on} · {count} migration"
            f"{'s' if count != 1 else ''} not applied yet",
            style="yellow",
        )
    return Text(f"Database {label} is on {on} · up to date", style="green")


def _split_warning(chain: Chain) -> Text:
    messages = {m.revision: m.message for m in chain.migrations}
    text = Text(f"⚠ The chain has split into {len(chain.heads)} ends:\n")
    for head in chain.heads:
        text.append(f"    {head}  {messages.get(head, '')}\n")
    text.append(
        'Upgrading to "head" fails until they are joined by a merge migration.'
    )
    return text


def _colored(line: str) -> Text:
    text = Text()
    for i, char in enumerate(line):
        color = LANE_COLORS[(i // 2) % len(LANE_COLORS)]
        text.append(char, style=f"bold {color}" if char == "●" else color)
    return text


def _tags(migration: Migration) -> str:
    tags = []
    if migration.is_head:
        tags.append("head")
    if migration.is_merge:
        tags.append("merge")
    if migration.is_branch_point:
        tags.append("split")
    if migration.is_base:
        tags.append("first")
    return ", ".join(tags)
