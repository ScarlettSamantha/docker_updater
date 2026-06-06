from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, RichLog, Static, Tree
from textual.widgets.tree import TreeNode

from docker_updater.compose import discover_stacks, inspect_remote_digest, short_digest
from docker_updater.config import AppConfig, ConfigStore
from docker_updater.docker_client import run_streamed_command
from docker_updater.models import ComposeServiceImage, ComposeStack, SecuritySummary
from docker_updater.screens import SettingsScreen, UpdateConfirmScreen
from docker_updater.security import scan_image_security


@dataclass(frozen=True)
class TreeData:
    kind: str
    stack_name: Optional[str] = None
    compose_service: Optional[str] = None


class ComposeServicesApp(App[None]):
    TITLE = "Docker Compose Service Manager"
    SUB_TITLE = "updates · container status · CVE checks"

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #status {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $text;
    }

    #toolbar {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }

    #legend {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }

    #services-tree {
        height: 1fr;
        padding: 0 1;
        border: round $primary;
        background: $background;
    }

    #log-title {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }

    #log {
        height: 12;
        padding: 0 1;
        border: round $surface;
        background: $background;
    }
    """

    BINDINGS = [
        Binding("d", "discover", "Discover"),
        Binding("c", "check_updates", "Check updates"),
        Binding("v", "scan_security", "Scan CVEs"),
        Binding("a", "select_updates", "Select updates"),
        Binding("space", "toggle_selected", "Select"),
        Binding("r", "toggle_restart", "Restart default"),
        Binding("l", "toggle_log", "Log"),
        Binding("x", "clear_log", "Clear log"),
        Binding("s", "settings", "Settings"),
        Binding("u", "update_selected", "Update selected"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config_store: ConfigStore, config: AppConfig) -> None:
        super().__init__()

        self.config_store = config_store
        self.base_directory = config.base_directory
        self.restart_after_update = config.restart_after_update
        self.log_visible = config.log_visible
        self.stacks: List[ComposeStack] = []
        self.selected_stack_names: Set[str] = set()
        self.busy = False
        self.status_widget: Static
        self.toolbar_widget: Static
        self.legend_widget: Static
        self.services_tree: Tree[TreeData]
        self.log_title_widget: Static
        self.log_widget: RichLog

    def compose(self) -> ComposeResult:
        yield Header()

        self.status_widget = Static("", id="status")
        yield self.status_widget

        self.toolbar_widget = Static("", id="toolbar")
        yield self.toolbar_widget

        self.legend_widget = Static("", id="legend")
        yield self.legend_widget

        self.services_tree = Tree("Services", id="services-tree")
        self.services_tree.show_root = False
        yield self.services_tree

        self.log_title_widget = Static("", id="log-title")
        yield self.log_title_widget

        self.log_widget = RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield self.log_widget

        yield Footer()

    async def on_mount(self) -> None:
        self.refresh_static_bars()
        self.refresh_log_visibility()
        self.write_log("[bold green]Docker Compose Service Manager started[/bold green]")
        await self.discover()

    async def action_discover(self) -> None:
        await self.discover()

    async def action_check_updates(self) -> None:
        await self.check_updates()

    async def action_scan_security(self) -> None:
        await self.scan_security()

    def action_select_updates(self) -> None:
        update_names = {
            stack.name
            for stack in self.stacks
            if stack.update_count > 0
        }

        if not update_names:
            self.write_log("[yellow]No checked updates available to select[/yellow]")
            return

        self.selected_stack_names = update_names
        self.refresh_tree()
        self.refresh_static_bars()
        self.write_log(f"[green]Selected[/green] {len(update_names)} service stack(s) with updates")

    async def action_update_selected(self) -> None:
        await self.update_selected()

    async def action_settings(self) -> None:
        if self.busy:
            return

        new_base_directory = await self.push_screen_wait(SettingsScreen(self.base_directory))

        if new_base_directory is None:
            return

        self.base_directory = new_base_directory
        self.selected_stack_names.clear()
        self.persist_config()
        self.write_log(f"[green]Services directory changed to[/green] {escape(str(new_base_directory))}")
        await self.discover()

    def action_toggle_restart(self) -> None:
        self.restart_after_update = not self.restart_after_update
        self.persist_config()
        self.refresh_tree()
        self.refresh_static_bars()
        restart = "enabled" if self.restart_after_update else "disabled"
        self.write_log(f"[green]Restart default {restart}[/green]")

    def action_toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        self.persist_config()
        self.refresh_log_visibility()
        self.refresh_static_bars()

        if self.log_visible:
            self.write_log("[green]Log panel shown[/green]")

    def action_clear_log(self) -> None:
        if not hasattr(self, "log_widget"):
            return

        self.log_widget.clear()
        self.write_log("[dim]Log cleared[/dim]")

    def action_toggle_selected(self) -> None:
        node = self.services_tree.cursor_node

        if node is None:
            return

        self.toggle_node(node)

    def on_tree_node_selected(self, event: Tree.NodeSelected[TreeData]) -> None:
        self.toggle_node(event.node)

    def toggle_node(self, node: TreeNode[TreeData]) -> None:
        data = node.data

        if data is None:
            return

        if data.kind == "image" and data.stack_name is not None:
            stack_name = data.stack_name
        elif data.kind == "stack" and data.stack_name is not None:
            stack_name = data.stack_name
        else:
            return

        if stack_name in self.selected_stack_names:
            self.selected_stack_names.remove(stack_name)
        else:
            self.selected_stack_names.add(stack_name)

        self.refresh_tree()
        self.refresh_static_bars()

    async def discover(self) -> None:
        if self.busy:
            return

        self.busy = True
        self.refresh_static_bars()
        self.write_log(f"[bold]Scanning[/bold] {escape(str(self.base_directory))}")

        if not self.base_directory.is_dir():
            self.stacks = []
            self.busy = False
            self.write_log(f"[red]Base directory does not exist:[/red] {escape(str(self.base_directory))}")
            self.refresh_tree()
            self.refresh_static_bars()
            return

        try:
            self.stacks = await discover_stacks(
                base_directory=self.base_directory,
                log_callback=self.log_command_line,
            )
        except OSError as exc:
            self.stacks = []
            self.busy = False
            self.write_log(f"[red]Could not scan base directory:[/red] {escape(str(exc))}")
            self.refresh_tree()
            self.refresh_static_bars()
            return

        valid_names = {stack.name for stack in self.stacks}
        self.selected_stack_names = {name for name in self.selected_stack_names if name in valid_names}
        self.busy = False
        self.refresh_tree()
        self.refresh_static_bars()
        self.write_log(f"[green]Found[/green] {len(self.stacks)} compose service folder(s)")

    async def check_updates(self) -> None:
        if self.busy:
            return

        self.busy = True
        self.refresh_static_bars()
        self.write_log("[bold]Checking remote image digests[/bold]")

        digest_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

        for stack in self.stacks:
            for image in stack.images:
                if image.image is None:
                    continue

                if image.image not in digest_cache:
                    self.write_log(f"[cyan]Checking[/cyan] {escape(image.image)}")
                    digest_cache[image.image] = await inspect_remote_digest(
                        image=image.image,
                        log_callback=self.log_command_line,
                    )

                image.remote_digest, image.remote_error = digest_cache[image.image]

                if image.remote_digest is not None and image.local_digest is not None:
                    image.update_available = image.remote_digest != image.local_digest
                elif image.remote_digest is not None and image.local_digest is None:
                    image.update_available = None
                else:
                    image.update_available = None

                self.refresh_tree()
                self.refresh_static_bars()

        self.busy = False
        self.refresh_tree()
        self.refresh_static_bars()

        updates = sum(
            1
            for stack in self.stacks
            for image in stack.images
            if image.update_available is True
        )

        if updates:
            self.write_log(f"[yellow]Updates available:[/yellow] {updates}")
        else:
            self.write_log("[green]No digest updates found[/green]")

    async def scan_security(self) -> None:
        if self.busy:
            return

        self.busy = True
        self.refresh_static_bars()
        self.write_log("[bold]Scanning local images for CVEs with Docker Scout[/bold]")

        security_cache: Dict[str, SecuritySummary] = {}

        for stack in self.stacks:
            for image in stack.images:
                if image.image is None:
                    continue

                if image.image not in security_cache:
                    self.write_log(f"[cyan]Scanning[/cyan] {escape(image.image)}")
                    security_cache[image.image] = await scan_image_security(
                        image=image.image,
                        log_callback=self.log_command_line,
                    )

                image.security = security_cache[image.image]
                self.refresh_tree()
                self.refresh_static_bars()

        self.busy = False
        self.refresh_tree()
        self.refresh_static_bars()

        warning_images = sum(
            1
            for stack in self.stacks
            for image in stack.images
            if image.security.has_warning
        )

        if warning_images:
            self.write_log(f"[red]Security warnings detected in[/red] {warning_images} image(s)")
        else:
            self.write_log("[green]No high/critical CVE warnings detected[/green]")

    async def update_selected(self) -> None:
        if self.busy:
            return

        selected_stacks = [
            stack
            for stack in self.stacks
            if stack.name in self.selected_stack_names
        ]

        if not selected_stacks:
            self.write_log("[yellow]No service folders selected[/yellow]")
            return

        restart_choice = await self.push_screen_wait(
            UpdateConfirmScreen(
                selected_count=len(selected_stacks),
                restart_default=self.restart_after_update,
                stack_names=[stack.name for stack in selected_stacks],
            )
        )

        if restart_choice is None:
            self.write_log("[dim]Update cancelled[/dim]")
            return

        self.restart_after_update = restart_choice
        self.persist_config()
        self.busy = True
        self.refresh_static_bars()

        for stack in selected_stacks:
            self.write_log(f"[bold]Updating[/bold] {escape(stack.name)}")
            pull_code = await run_streamed_command(
                ["docker", "compose", "-f", str(stack.compose_file), "pull", "--ignore-buildable"],
                cwd=stack.directory,
                line_callback=self.log_command_line,
            )

            if pull_code != 0:
                self.write_log(f"[yellow]Retrying without --ignore-buildable for[/yellow] {escape(stack.name)}")
                pull_code = await run_streamed_command(
                    ["docker", "compose", "-f", str(stack.compose_file), "pull"],
                    cwd=stack.directory,
                    line_callback=self.log_command_line,
                )

            if pull_code != 0:
                self.write_log(f"[red]Pull failed for[/red] {escape(stack.name)}")
                continue

            if self.restart_after_update:
                self.write_log(f"[bold]Restarting[/bold] {escape(stack.name)}")
                up_code = await run_streamed_command(
                    ["docker", "compose", "-f", str(stack.compose_file), "up", "-d", "--remove-orphans"],
                    cwd=stack.directory,
                    line_callback=self.log_command_line,
                )

                if up_code != 0:
                    self.write_log(f"[red]Restart failed for[/red] {escape(stack.name)}")
                    continue

            else:
                self.write_log(f"[dim]Restart skipped for {escape(stack.name)}[/dim]")

        self.busy = False
        await self.discover()
        await self.check_updates()

    def refresh_tree(self) -> None:
        if not hasattr(self, "services_tree"):
            return

        self.services_tree.root.remove_children()

        if not self.stacks:
            self.services_tree.root.add(
                "[dim]No compose service folders found. Press s to change settings or d to rescan.[/dim]",
                data=TreeData(kind="empty"),
            )
            self.services_tree.root.expand()
            return

        for stack in self.stacks:
            stack_node = self.services_tree.root.add(
                self.stack_label(stack),
                data=TreeData(kind="stack", stack_name=stack.name),
            )

            if stack.load_error is not None:
                stack_node.add(
                    f"[red]compose error[/red] [dim]{escape(stack.load_error)}[/dim]",
                    data=TreeData(kind="image", stack_name=stack.name),
                )

            for image in stack.images:
                stack_node.add(
                    self.image_label(image),
                    data=TreeData(
                        kind="image",
                        stack_name=stack.name,
                        compose_service=image.compose_service,
                    ),
                )

            stack_node.expand()

        self.services_tree.root.expand()

    def refresh_static_bars(self) -> None:
        self.refresh_status()
        self.refresh_toolbar()
        self.refresh_legend()
        self.refresh_log_title()

    def refresh_status(self) -> None:
        if not hasattr(self, "status_widget"):
            return

        total_stacks = len(self.stacks)
        total_images = sum(len(stack.images) for stack in self.stacks)
        selected = len(self.selected_stack_names)
        updates = sum(
            1
            for stack in self.stacks
            for image in stack.images
            if image.update_available is True
        )
        warnings = sum(
            1
            for stack in self.stacks
            for image in stack.images
            if image.security.has_warning
        )
        running, partial, stopped, unknown = self.container_state_counts()
        busy = "[yellow]busy[/yellow]" if self.busy else "[green]ready[/green]"

        self.status_widget.update(
            f"{busy}  "
            f"[bold]{escape(self.truncate_path(self.base_directory))}[/bold]  "
            f"stacks [bold]{total_stacks}[/bold]  "
            f"images [bold]{total_images}[/bold]  "
            f"selected [bold]{selected}[/bold]  "
            f"updates [yellow]{updates}[/yellow]  "
            f"security [red]{warnings}[/red]  "
            f"running [green]{running}[/green]  "
            f"partial [yellow]{partial}[/yellow]  "
            f"stopped [red]{stopped}[/red]  "
            f"unknown [dim]{unknown}[/dim]"
        )

    def refresh_toolbar(self) -> None:
        if not hasattr(self, "toolbar_widget"):
            return

        restart = "[green]yes[/green]" if self.restart_after_update else "[red]no[/red]"
        log = "[green]shown[/green]" if self.log_visible else "[dim]hidden[/dim]"

        self.toolbar_widget.update(
            "[bold]Actions[/bold]  "
            "[cyan]d[/cyan] rescan  "
            "[cyan]c[/cyan] updates  "
            "[cyan]v[/cyan] CVEs  "
            "[cyan]a[/cyan] select updateable  "
            "[cyan]space[/cyan] toggle  "
            "[cyan]u[/cyan] update  "
            "[cyan]s[/cyan] settings  "
            "[cyan]l[/cyan] log  "
            "[cyan]x[/cyan] clear log  "
            f"restart default {restart}  "
            f"log {log}"
        )

    def refresh_legend(self) -> None:
        if not hasattr(self, "legend_widget"):
            return

        self.legend_widget.update(
            "[bold]Legend[/bold]  "
            "[green]●[/green] running  "
            "[yellow]◐[/yellow] partial/other  "
            "[red]○[/red] stopped  "
            "[dim]?[/dim] unknown  "
            "[yellow]update[/yellow] remote digest changed  "
            "[red]⚠[/red] high/critical CVEs"
        )

    def refresh_log_title(self) -> None:
        if not hasattr(self, "log_title_widget"):
            return

        status = "visible" if self.log_visible else "hidden"
        self.log_title_widget.update(
            f"[bold]Command log[/bold] [dim]({status}, press l to toggle, x to clear)[/dim]"
        )

    def refresh_log_visibility(self) -> None:
        if not hasattr(self, "log_widget") or not hasattr(self, "log_title_widget"):
            return

        display = "block" if self.log_visible else "none"
        self.log_title_widget.styles.display = display
        self.log_widget.styles.display = display

    def stack_label(self, stack: ComposeStack) -> str:
        selected = "[green]☑[/green]" if stack.name in self.selected_stack_names else "[dim]☐[/dim]"
        state = self.stack_state_indicator(stack)
        update_text = self.stack_update_label(stack)
        security_text = self.security_label(stack.security_totals)

        return (
            f"{selected} {state} [bold]{escape(stack.name)}[/bold] "
            f"[dim]· {len(stack.images)} item(s) · {escape(stack.compose_file.name)}[/dim] "
            f"{update_text}{security_text}"
        )

    def image_label(self, image: ComposeServiceImage) -> str:
        state = self.container_state_indicator(image)
        service_name = escape(image.compose_service)

        if image.image is None:
            if image.is_build_only:
                return f"    {state} [bold]{service_name}[/bold] [dim]build-only image, remote update check skipped[/dim]"

            return f"    {state} [bold]{service_name}[/bold] [dim]no image configured[/dim]"

        image_name = escape(image.image)
        current = self.current_version_label(image)
        remote = self.remote_version_label(image)
        update_status = self.image_update_label(image)
        container = f" [dim]· {escape(image.container_name)}[/dim]" if image.container_name else ""
        security = self.security_label(image.security)

        if remote is not None:
            return (
                f"    {state} [bold]{service_name}[/bold] "
                f"[dim]{image_name}[/dim] "
                f"{current} [dim]→[/dim] {remote} "
                f"{update_status}{security}{container}"
            )

        return (
            f"    {state} [bold]{service_name}[/bold] "
            f"[dim]{image_name}[/dim] "
            f"{current} "
            f"{update_status}{security}{container}"
        )

    def stack_state_indicator(self, stack: ComposeStack) -> str:
        states = [
            image.container_state
            for image in stack.images
            if image.container_state is not None
        ]

        if not states:
            return "[dim]?[/dim]"

        running_count = sum(1 for state in states if state.lower() == "running")

        if running_count == len(states):
            return "[green]●[/green]"

        if running_count > 0:
            return "[yellow]◐[/yellow]"

        return "[red]○[/red]"

    def container_state_indicator(self, image: ComposeServiceImage) -> str:
        state = (image.container_state or "").lower()
        health = (image.container_health or "").lower()

        if state == "running" and health == "unhealthy":
            return "[red]●[/red]"

        if state == "running":
            return "[green]●[/green]"

        if state in {"exited", "stopped", "dead"}:
            return "[red]○[/red]"

        if state:
            return "[yellow]◐[/yellow]"

        return "[dim]?[/dim]"

    def stack_update_label(self, stack: ComposeStack) -> str:
        updates = stack.update_count
        checked = any(image.remote_digest is not None or image.remote_error is not None for image in stack.images)

        if updates:
            return f"[yellow] {updates} update(s)[/yellow]"

        if checked:
            return "[green] current[/green]"

        return "[dim] not checked[/dim]"

    def image_update_label(self, image: ComposeServiceImage) -> str:
        if image.update_available is True:
            return "[yellow]update[/yellow]"

        if image.update_available is False:
            return "[green]current[/green]"

        if image.remote_error is not None:
            return f"[red]unknown[/red] [dim]{escape(image.remote_error)}[/dim]"

        if image.local_error is not None:
            return f"[yellow]not local[/yellow] [dim]{escape(image.local_error)}[/dim]"

        return "[dim]not checked[/dim]"

    def security_label(self, summary: SecuritySummary) -> str:
        if summary.error is not None:
            return f" [yellow]security unknown[/yellow] [dim]{escape(summary.error)}[/dim]"

        if not summary.scanned:
            return ""

        if summary.total == 0:
            return " [green]security ok[/green]"

        label = f"C:{summary.critical} H:{summary.high} M:{summary.medium} L:{summary.low}"

        if summary.has_warning:
            return f" [red]⚠ {label}[/red]"

        return f" [yellow]security {label}[/yellow]"

    def current_version_label(self, image: ComposeServiceImage) -> str:
        if image.local_digest is not None:
            return f"[dim]{image.tag}@[/dim]{short_digest(image.local_digest)}"

        if image.local_id is not None:
            return f"[dim]{image.tag}@[/dim]{short_digest(image.local_id)}"

        return f"[dim]{image.tag}@not-local[/dim]"

    def remote_version_label(self, image: ComposeServiceImage) -> Optional[str]:
        if image.remote_digest is None:
            return None

        return f"[yellow]{image.tag}@{short_digest(image.remote_digest)}[/yellow]"

    def container_state_counts(self) -> Tuple[int, int, int, int]:
        running = 0
        partial = 0
        stopped = 0
        unknown = 0

        for stack in self.stacks:
            for image in stack.images:
                state = (image.container_state or "").lower()

                if state == "running":
                    running += 1
                elif state in {"exited", "stopped", "dead"}:
                    stopped += 1
                elif state:
                    partial += 1
                else:
                    unknown += 1

        return running, partial, stopped, unknown

    def truncate_path(self, path: Path, max_length: int = 46) -> str:
        value = str(path)

        if len(value) <= max_length:
            return value

        return f"…{value[-max_length + 1:]}"

    def persist_config(self) -> None:
        self.config_store.save(
            AppConfig(
                base_directory=self.base_directory,
                restart_after_update=self.restart_after_update,
                log_visible=self.log_visible,
            )
        )

    def log_command_line(self, message: str) -> None:
        escaped = escape(message)

        if message.startswith("$ "):
            self.write_log(f"[cyan]▶[/cyan] [bold]{escaped}[/bold]")
            return

        if message.startswith("exit code: 0"):
            self.write_log(f"[green]✓[/green] [dim]{escaped}[/dim]")
            return

        if message.startswith("exit code:"):
            self.write_log(f"[red]✗[/red] [dim]{escaped}[/dim]")
            return

        self.write_log(f"[dim]│ {escaped}[/dim]")

    def write_log(self, message: str) -> None:
        if not hasattr(self, "log_widget"):
            return

        self.log_widget.write(message)