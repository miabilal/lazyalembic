"""The main lazyalembic screen."""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class LazyAlembicApp(App):
    """Terminal map of an Alembic migration chain."""

    TITLE = "lazyalembic"
    SUB_TITLE = "migration map"

    CSS = """
    #chain {
        padding: 1 2;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Your migration chain will appear here.", id="chain")
        yield Footer()
