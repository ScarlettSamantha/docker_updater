from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Static


class SettingsScreen(ModalScreen[Optional[Path]]):
    CSS = """
    SettingsScreen {
        align: center middle;
    }

    #settings-dialog {
        width: 86;
        height: 15;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }

    #settings-title {
        height: 1;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    #settings-help {
        height: 2;
        color: $text-muted;
        margin-bottom: 1;
    }

    #base-directory {
        margin-top: 1;
    }

    #settings-error {
        height: 1;
        color: $error;
        margin-top: 1;
    }

    #settings-buttons {
        height: 3;
        margin-top: 1;
    }

    Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, base_directory: Path) -> None:
        super().__init__()

        self.base_directory = base_directory

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Label("Settings", id="settings-title")
            yield Static(
                "Choose the parent directory containing one subfolder per Docker Compose service.",
                id="settings-help",
            )
            yield Label("Services base directory")
            yield Input(
                value=str(self.base_directory),
                placeholder="/services",
                id="base-directory",
            )
            yield Static("", id="settings-error")

            with Horizontal(id="settings-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#base-directory", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.save()
            return

        if event.button.id == "cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "base-directory":
            self.save()

    def save(self) -> None:
        path_input = self.query_one("#base-directory", Input)
        error = self.query_one("#settings-error", Static)
        raw_value = path_input.value.strip()

        if not raw_value:
            error.update("Enter a directory path")
            return

        path = Path(raw_value).expanduser().resolve()

        if not path.is_dir():
            error.update("Directory does not exist or is not readable")
            return

        self.dismiss(path)


class UpdateConfirmScreen(ModalScreen[Optional[bool]]):
    CSS = """
    UpdateConfirmScreen {
        align: center middle;
    }

    #update-dialog {
        width: 88;
        height: 18;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }

    #update-title {
        height: 1;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    #update-message {
        height: 2;
        color: $text;
    }

    #stack-preview {
        height: 5;
        color: $text-muted;
        margin-top: 1;
    }

    #restart-checkbox {
        margin-top: 1;
    }

    #update-buttons {
        height: 3;
        margin-top: 1;
    }

    Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        selected_count: int,
        restart_default: bool,
        stack_names: Optional[List[str]] = None,
    ) -> None:
        super().__init__()

        self.selected_count = selected_count
        self.restart_default = restart_default
        self.stack_names = stack_names or []

    def compose(self) -> ComposeResult:
        plural = "service stack" if self.selected_count == 1 else "service stacks"

        with Vertical(id="update-dialog"):
            yield Label("Confirm update", id="update-title")
            yield Static(
                f"Pull updates for {self.selected_count} selected {plural}.",
                id="update-message",
            )
            yield Static(self.stack_preview(), id="stack-preview")
            yield Checkbox(
                "Restart containers after pulling",
                value=self.restart_default,
                id="restart-checkbox",
            )

            with Horizontal(id="update-buttons"):
                yield Button("Update", variant="warning", id="update")
                yield Button("Cancel", id="cancel")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "update":
            checkbox = self.query_one("#restart-checkbox", Checkbox)
            self.dismiss(bool(checkbox.value))
            return

        if event.button.id == "cancel":
            self.dismiss(None)

    def stack_preview(self) -> str:
        if not self.stack_names:
            return "Selected stacks: unavailable"

        shown = self.stack_names[:4]
        remaining = len(self.stack_names) - len(shown)
        lines = [f"• {name}" for name in shown]

        if remaining > 0:
            lines.append(f"• … and {remaining} more")

        return "\n".join(lines)