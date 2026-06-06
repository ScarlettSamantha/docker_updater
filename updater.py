import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from docker_updater.app import ComposeServicesApp
from docker_updater.config import ConfigStore, load_config_with_first_run_prompt


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        default=None,
        help="Override and store the services base directory",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Override the config file path",
    )

    return parser.parse_args(argv)


def config_store_from_arg(config_path: Optional[str]) -> ConfigStore:
    if config_path is None:
        return ConfigStore.default()

    return ConfigStore(Path(config_path).expanduser().resolve())


def main() -> None:
    args = parse_args(sys.argv[1:])
    config_store = config_store_from_arg(args.config)
    config = load_config_with_first_run_prompt(
        config_store=config_store,
        base_override=args.base,
    )

    app = ComposeServicesApp(
        config_store=config_store,
        config=config,
    )
    app.run()


if __name__ == "__main__":
    main()