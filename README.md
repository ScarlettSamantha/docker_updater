# Docker Compose Service Manager

A small Python TUI for managing a folder full of Docker Compose services.

It scans a parent directory such as `/services`, finds service folders containing `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, or `compose.yaml`, then shows each stack with its configured images, current local digests, remote update status, container runtime state, and optional CVE warnings through Docker Scout.

## Features

- Scans a parent services directory, for example `/services`
- Detects Docker Compose stacks automatically
- Shows each service stack as a top-level item
- Shows images/containers underneath each stack
- Displays local image tag and digest
- Checks remote image digests to detect updates
- Selects all stacks with available updates
- Pulls selected stacks
- Optionally restarts containers after pulling
- Shows running/stopped/unknown container status inline
- Optional Docker Scout CVE scan
- Inline security warning summary
- Toggleable live command log
- First-run setup prompt
- Persistent config stored under `~/.config/docker-updater/config.json`
- Settings dialog for changing the services directory

## Requirements

- Python 3.11+
- Docker
- Docker Compose v2
- A directory containing one subdirectory per Compose service
- Optional: Docker Scout for CVE scanning

Python dependencies:

```bash
pip install textual PyYAML
```

Or install from the included requirements file:

```bash
pip install -r requirements.txt
```

## Project layout

```text
docker-updater/
├── updater.py
├── requirements.txt
└── docker_updater/
    ├── __init__.py
    ├── app.py
    ├── compose.py
    ├── config.py
    ├── docker_client.py
    ├── models.py
    ├── screens.py
    └── security.py
```

## Installation

Clone the repository:

```bash
git clone <your-repo-url>
cd docker-updater
```

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
python3 updater.py
```

On first run, the app asks for the parent directory containing your service folders.

Example:

```text
Docker Compose services directory is not configured yet.
This should be the folder containing one subfolder per service, for example /services.
Services directory [/services]:
```

## Usage

Start the TUI:

```bash
python3 updater.py
```

Override and save the services directory:

```bash
python3 updater.py --base /services
```

Use a custom config file:

```bash
python3 updater.py --config ./config.json
```

## Keybindings

| Key | Action |
| --- | --- |
| `d` | Rescan services directory |
| `c` | Check remote image updates |
| `v` | Scan local images for CVEs using Docker Scout |
| `a` | Select all stacks with available updates |
| `space` | Toggle selected stack |
| `u` | Update selected stacks |
| `r` | Toggle default restart option |
| `l` | Show/hide command log |
| `x` | Clear command log |
| `s` | Open settings |
| `q` | Quit |

## How update detection works

The app checks updates by comparing local image digests with remote image digests.

This is more reliable than comparing tags, because many self-hosted services use tags like:

```text
latest
stable
main
nightly
develop
```

Those tags often stay the same while the image behind them changes.

Example:

```text
latest@sha256:old123 → latest@sha256:new456
```

That means the remote image behind the same tag has changed and an update is available.

## Updating services

Select one or more stacks with `space`, or press `a` to select all stacks that have checked updates.

Then press `u`.

Before updating, the app asks for confirmation and shows a checkbox:

```text
Restart containers after pulling
```

The default is remembered in the config file. When enabled, the app runs:

```bash
docker compose pull
docker compose up -d --remove-orphans
```

When disabled, the app only pulls updated images and does not restart containers.

## Container status indicators

| Indicator | Meaning |
| --- | --- |
| `●` green | Running |
| `◐` yellow | Partial, mixed, or other state |
| `○` red | Stopped/exited/dead |
| `?` dim | Unknown |

For stacks, the indicator is aggregated from the containers inside the stack.

## Security scanning

Security scanning is optional and uses Docker Scout when available.

Press:

```text
v
```

The app runs Docker Scout against local images and parses the output for CVE severity counts.

Security labels look like this:

```text
security ok
⚠ C:1 H:3 M:8 L:12
security unknown
```

High and critical CVEs are shown as warnings.

Docker Scout must be installed and usable by your Docker CLI. If Scout is missing, unavailable, or not authenticated, the app shows security as unknown instead of crashing.

## Command log

The app includes a live command log panel.

It shows commands being executed and Docker output while updates/restarts are running.

Toggle it with:

```text
l
```

Clear it with:

```text
x
```

The log visibility preference is saved in the config file.

## Configuration

Config is stored at:

```text
~/.config/docker-updater/config.json
```

Example config:

```json
{
  "base_directory": "/services",
  "log_visible": true,
  "restart_after_update": true
}
```

You can change the services directory inside the TUI with:

```text
s
```

## Expected services directory structure

The app expects a parent directory where each service has its own folder.

Example:

```text
/services/
|
├── nginx/
│   └── compose.yaml
└── postgres/
    └── docker-compose.yaml
```

Each service folder is treated as one Compose stack.

## Notes

This tool intentionally shells out to the Docker CLI instead of talking directly to the Docker daemon API. That keeps behavior close to what you would manually run in the terminal and avoids needing a separate Docker SDK layer.

The app does not automatically update anything in the background. Updates only happen when you select stacks and confirm the update action.
