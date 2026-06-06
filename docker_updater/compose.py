import json
import platform
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, cast

from docker_updater.docker_client import run_command
from docker_updater.models import ComposeServiceImage, ComposeStack

yaml_module: Any

try:
    import yaml as yaml_module  # type: ignore[import-untyped]
except ModuleNotFoundError:
    yaml_module = None


COMPOSE_FILE_NAMES: Tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)


JsonObject = Dict[str, Any]
ContainerStatus = Dict[str, Optional[str]]
LogCallback = Callable[[str], None]


def find_compose_file(directory: Path) -> Optional[Path]:
    for file_name in COMPOSE_FILE_NAMES:
        compose_file = directory / file_name

        if compose_file.is_file():
            return compose_file

    return None


def as_json_object(value: Any) -> Optional[JsonObject]:
    if isinstance(value, dict):
        return cast(JsonObject, value)

    return None


def as_json_object_list(value: Any) -> List[JsonObject]:
    if isinstance(value, list):
        return [cast(JsonObject, item) for item in value if isinstance(item, dict)] # type: ignore[list-item]

    if isinstance(value, dict):
        return [cast(JsonObject, value)]

    return []


def get_string(mapping: Mapping[str, Any], key: str) -> Optional[str]:
    value = mapping.get(key)

    if isinstance(value, str):
        return value

    return None


def get_mapping(mapping: Mapping[str, Any], key: str) -> Optional[JsonObject]:
    return as_json_object(mapping.get(key))


def get_list(mapping: Mapping[str, Any], key: str) -> Optional[List[Any]]:
    value = mapping.get(key)

    if isinstance(value, list):
        return value # type: ignore[return-value]

    return None


def image_tag(image: str) -> str:
    image_without_digest = image.split("@", 1)[0]
    last_slash = image_without_digest.rfind("/")
    last_colon = image_without_digest.rfind(":")

    if last_colon > last_slash:
        return image_without_digest[last_colon + 1 :]

    return "latest"


def image_repository(image: str) -> str:
    image_without_digest = image.split("@", 1)[0]
    last_slash = image_without_digest.rfind("/")
    last_colon = image_without_digest.rfind(":")

    if last_colon > last_slash:
        return image_without_digest[:last_colon]

    return image_without_digest


def repository_candidates(repository: str) -> Set[str]:
    first_part = repository.split("/", 1)[0]
    candidates: Set[str] = {repository}
    has_registry = "." in first_part or ":" in first_part or first_part == "localhost"

    if not has_registry:
        candidates.add(f"docker.io/{repository}")

        if "/" not in repository:
            candidates.add(f"library/{repository}")
            candidates.add(f"docker.io/library/{repository}")

    return candidates


def choose_repo_digest(image: str, repo_digests: List[str]) -> Optional[str]:
    repository = image_repository(image)
    candidates = repository_candidates(repository)

    for repo_digest in repo_digests:
        if "@" not in repo_digest:
            continue

        digest_repo, digest = repo_digest.split("@", 1)

        for candidate in candidates:
            if digest_repo == candidate or digest_repo.endswith(f"/{candidate}") or candidate.endswith(f"/{digest_repo}"):
                return digest

    for repo_digest in repo_digests:
        if "@" in repo_digest:
            return repo_digest.split("@", 1)[1]

    return None


def short_digest(value: Optional[str]) -> str:
    if value is None:
        return "?"

    if value.startswith("sha256:"):
        return f"sha256:{value[7:19]}"

    return value[:18]


def host_architecture() -> str:
    machine = platform.machine().lower()

    if machine in {"x86_64", "amd64"}:
        return "amd64"

    if machine in {"aarch64", "arm64"}:
        return "arm64"

    if machine.startswith("armv7"):
        return "arm/v7"

    return machine


def extract_remote_digest(payload: Any) -> Optional[str]:
    if isinstance(payload, list):
        for item in payload: # type: ignore[iteration-over-possibly-noniterable]
            digest = extract_remote_digest(item)

            if digest is not None:
                return digest

        return None

    payload_mapping = as_json_object(payload)

    if payload_mapping is None:
        return None

    descriptor = get_mapping(payload_mapping, "Descriptor")

    if descriptor is not None:
        descriptor_digest = get_string(descriptor, "digest")

        if descriptor_digest is not None:
            return descriptor_digest

    digest = get_string(payload_mapping, "digest")

    if digest is not None:
        return digest

    manifests = get_list(payload_mapping, "manifests")

    if manifests is None:
        return None

    arch = host_architecture()

    for manifest_value in manifests:
        manifest = as_json_object(manifest_value)

        if manifest is None:
            continue

        manifest_platform = get_mapping(manifest, "platform")

        if manifest_platform is None:
            continue

        if get_string(manifest_platform, "os") == "linux" and get_string(manifest_platform, "architecture") == arch:
            manifest_digest = get_string(manifest, "digest")

            if manifest_digest is not None:
                return manifest_digest

    for manifest_value in manifests:
        manifest = as_json_object(manifest_value)

        if manifest is None:
            continue

        manifest_digest = get_string(manifest, "digest")

        if manifest_digest is not None:
            return manifest_digest

    return None


async def inspect_local_image(
    image: str,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    result = await run_command(
        ["docker", "image", "inspect", image],
        timeout=30.0,
        line_callback=log_callback,
    )

    if result.code != 0:
        return None, None, result.stderr.strip() or result.stdout.strip()

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, None, str(exc)

    image_data_list = as_json_object_list(payload)

    if not image_data_list:
        return None, None, "docker image inspect returned no image data"

    image_data = image_data_list[0]
    image_id = get_string(image_data, "Id")
    repo_digests_value = image_data.get("RepoDigests")
    repo_digests: List[str] = []

    if isinstance(repo_digests_value, list):
        repo_digests = [value for value in repo_digests_value if isinstance(value, str)] # type: ignore[list-item]

    digest = choose_repo_digest(
        image=image,
        repo_digests=repo_digests,
    )

    return image_id, digest, None


async def inspect_remote_digest(
    image: str,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[Optional[str], Optional[str]]:
    result = await run_command(
        ["docker", "manifest", "inspect", "--verbose", image],
        timeout=60.0,
        line_callback=log_callback,
    )

    if result.code != 0:
        return None, result.stderr.strip() or result.stdout.strip()

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, str(exc)

    digest = extract_remote_digest(payload)

    if digest is None:
        return None, "No remote digest found in manifest"

    return digest, None


async def inspect_compose_containers(
    compose_file: Path,
    log_callback: Optional[LogCallback] = None,
) -> Dict[str, ContainerStatus]:
    result = await run_command(
        ["docker", "compose", "-f", str(compose_file), "ps", "--all", "--format", "json"],
        cwd=compose_file.parent,
        timeout=30.0,
        line_callback=log_callback,
    )

    if result.code != 0:
        return {}

    payload = parse_compose_ps_json(result.stdout)
    containers: Dict[str, ContainerStatus] = {}

    for item in payload:
        service = get_string(item, "Service")

        if service is None:
            continue

        containers[service] = {
            "name": get_string(item, "Name"),
            "state": get_string(item, "State"),
            "health": get_string(item, "Health"),
        }

    return containers


def parse_compose_ps_json(raw_value: str) -> List[JsonObject]:
    raw_value = raw_value.strip()

    if not raw_value:
        return []

    try:
        parsed = json.loads(raw_value)
        parsed_items = as_json_object_list(parsed)

        if parsed_items:
            return parsed_items
    except json.JSONDecodeError:
        pass

    items: List[JsonObject] = []

    for line in raw_value.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            parsed_line = json.loads(line)
        except json.JSONDecodeError:
            continue

        parsed_line_object = as_json_object(parsed_line)

        if parsed_line_object is not None:
            items.append(parsed_line_object)

    return items


async def resolve_compose_services(
    compose_file: Path,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[List[ComposeServiceImage], Optional[str]]:
    result = await run_command(
        ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
        cwd=compose_file.parent,
        timeout=30.0,
        line_callback=log_callback,
    )

    if result.code == 0:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return [], str(exc)

        payload_mapping = as_json_object(payload)

        if payload_mapping is None:
            return [], "Compose config did not contain an object"

        services = get_mapping(payload_mapping, "services")

        if services is None:
            return [], "Compose config did not contain a services map"

        return service_images_from_services_map(services), None

    if yaml_module is None:
        return [], result.stderr.strip() or result.stdout.strip()

    try:
        raw_payload: Dict[str, Any] = yaml_module.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [], str(exc)

    raw_payload_mapping = as_json_object(raw_payload)

    if raw_payload_mapping is None:
        return [], "Compose YAML did not contain an object"

    raw_services = get_mapping(raw_payload_mapping, "services")

    if raw_services is None:
        return [], "Compose YAML did not contain a services map"

    return service_images_from_services_map(raw_services), None


def service_images_from_services_map(services: Mapping[str, Any]) -> List[ComposeServiceImage]:
    service_images: List[ComposeServiceImage] = []

    for service_name, service_config_value in sorted(services.items()):
        service_config = as_json_object(service_config_value)

        if service_config is None:
            continue

        image = get_string(service_config, "image")
        container_name = get_string(service_config, "container_name")
        is_build_only = image is None and service_config.get("build") is not None

        service_images.append(
            ComposeServiceImage(
                compose_service=str(service_name),
                image=image,
                container_name=container_name,
                is_build_only=is_build_only,
                tag=image_tag(image) if image is not None else "local",
            )
        )

    return service_images


async def discover_stacks(
    base_directory: Path,
    log_callback: Optional[LogCallback] = None,
) -> List[ComposeStack]:
    stacks: List[ComposeStack] = []

    for directory in sorted(base_directory.iterdir()):
        if not directory.is_dir():
            continue

        compose_file = find_compose_file(directory)

        if compose_file is None:
            continue

        if log_callback is not None:
            log_callback(f"Loading {directory.name}")

        stack = ComposeStack(
            name=directory.name,
            directory=directory,
            compose_file=compose_file,
        )
        stack.images, stack.load_error = await resolve_compose_services(
            compose_file=compose_file,
            log_callback=log_callback,
        )
        container_statuses = await inspect_compose_containers(
            compose_file=compose_file,
            log_callback=log_callback,
        )

        for image in stack.images:
            container_status = container_statuses.get(image.compose_service)

            if container_status is not None:
                image.container_name = container_status.get("name") or image.container_name
                image.container_state = container_status.get("state")
                image.container_health = container_status.get("health")

            if image.image is None:
                continue

            image.local_id, image.local_digest, image.local_error = await inspect_local_image(
                image=image.image,
                log_callback=log_callback,
            )

        stacks.append(stack)

    return stacks