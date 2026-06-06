import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AppConfig:
    base_directory: Path
    restart_after_update: bool = True
    log_visible: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        base_directory = data.get("base_directory")
        restart_after_update = data.get("restart_after_update", True)
        log_visible = data.get("log_visible", True)

        if not isinstance(base_directory, str):
            raise ValueError("Config value base_directory must be a string")

        if not isinstance(restart_after_update, bool):
            restart_after_update = True

        if not isinstance(log_visible, bool):
            log_visible = True

        return cls(
            base_directory=Path(base_directory).expanduser().resolve(),
            restart_after_update=restart_after_update,
            log_visible=log_visible,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_directory": str(self.base_directory),
            "restart_after_update": self.restart_after_update,
            "log_visible": self.log_visible,
        }


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def default(cls) -> "ConfigStore":
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        config_root = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"

        return cls(config_root / "docker-updater" / "config.json")

    def load(self) -> Optional[AppConfig]:
        if not self.path.is_file():
            return None

        try:
            payload: Dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict): # type: ignore[unreachable]
            return None

        try:
            return AppConfig.from_dict(payload)
        except ValueError:
            return None

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def resolve_existing_directory(raw_value: str) -> Optional[Path]:
    value = raw_value.strip()

    if not value:
        return None

    path = Path(value).expanduser().resolve()

    if not path.is_dir():
        return None

    return path


def prompt_for_base_directory(default: Path) -> Path:
    print("Docker Compose services directory is not configured yet.")
    print("This should be the folder containing one subfolder per service, for example /services.")

    while True:
        raw_value = input(f"Services directory [{default}]: ").strip()
        value = raw_value or str(default)
        path = resolve_existing_directory(value)

        if path is not None:
            return path

        print(f"Directory does not exist or is not readable: {value}")


def load_config_with_first_run_prompt(
    config_store: ConfigStore,
    base_override: Optional[str] = None,
) -> AppConfig:
    existing_config = config_store.load()

    if base_override is not None:
        override_directory = resolve_existing_directory(base_override)

        if override_directory is None:
            raise SystemExit(f"Invalid services directory: {base_override}")

        config = AppConfig(
            base_directory=override_directory,
            restart_after_update=existing_config.restart_after_update if existing_config else True,
            log_visible=existing_config.log_visible if existing_config else True,
        )
        config_store.save(config)

        return config

    if existing_config is not None and existing_config.base_directory.is_dir():
        return existing_config

    default_directory = existing_config.base_directory if existing_config else Path("/services")
    base_directory = prompt_for_base_directory(default_directory)
    config = AppConfig(
        base_directory=base_directory,
        restart_after_update=existing_config.restart_after_update if existing_config else True,
        log_visible=existing_config.log_visible if existing_config else True,
    )
    config_store.save(config)

    return config