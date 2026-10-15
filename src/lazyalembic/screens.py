"""Pop-up boxes: confirm a change before running it, then show what happened."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

_BOX_CSS = """
{name} {{
    align: center middle;
}}
{name} > Vertical {{
    width: 90%;
    max-width: 100;
    height: auto;
    max-height: 90%;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}}
{name} .title {{
    text-style: bold;
    margin-bottom: 1;
}}
{name} .command {{
    margin: 1 0;
    padding: 0 1;
    background: $panel;
}}
{name} Horizontal {{
    height: auto;
    margin-top: 1;
}}
{name} Button {{
    margin-right: 2;
}}
"""


class ConfirmScreen(ModalScreen[bool]):
    """Show what a change will do and the exact command; run it only on yes."""

    DEFAULT_CSS = _BOX_CSS.format(name="ConfirmScreen")
    BINDINGS = [("y", "answer(True)", "Yes"), ("n,escape", "answer(False)", "No")]

    def __init__(self, title: str, body: Text, command: str, where: str) -> None:
        super().__init__()
        self.box_title = title
        self.body = body
        self.command = command
        self.where = where

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.box_title, classes="title")
            yield Static(self.body)
            yield Static(Text(f"$ {self.command}"), classes="command", id="command")
            yield Static(Text(f"Runs in {self.where}", style="dim"))
            with Horizontal():
                yield Button("Yes, run it (y)", variant="warning", id="yes")
                yield Button("Cancel (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


class ResultScreen(ModalScreen[None]):
    """What a command printed, and whether it worked."""

    DEFAULT_CSS = _BOX_CSS.format(name="ResultScreen") + """
    ResultScreen VerticalScroll {
        height: auto;
        max-height: 20;
    }
    """
    BINDINGS = [("escape,enter", "close", "Close")]

    def __init__(self, title: str, command: str, output: str, message: str = "") -> None:
        super().__init__()
        self.box_title = title
        self.command = command
        self.output = output
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.box_title, classes="title", id="title")
            if self.message:
                yield Static(Text(self.message), id="message")
            yield Static(Text(f"$ {self.command}"), classes="command")
            with VerticalScroll():
                yield Static(Text(self.output or "(no output)", style="dim"), id="output")
            with Horizontal():
                yield Button("Close (enter)", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()
