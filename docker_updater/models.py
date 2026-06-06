from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


def _compose_service_images_factory() -> List["ComposeServiceImage"]:
    return []


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


@dataclass
class SecuritySummary:
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unspecified: int = 0
    error: Optional[str] = None
    scanned: bool = False

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low + self.unspecified

    @property
    def has_warning(self) -> bool:
        return self.critical > 0 or self.high > 0


@dataclass
class ComposeServiceImage:
    compose_service: str
    image: Optional[str]
    container_name: Optional[str] = None
    is_build_only: bool = False
    tag: str = "latest"
    local_id: Optional[str] = None
    local_digest: Optional[str] = None
    remote_digest: Optional[str] = None
    local_error: Optional[str] = None
    remote_error: Optional[str] = None
    update_available: Optional[bool] = None
    container_state: Optional[str] = None
    container_health: Optional[str] = None
    security: SecuritySummary = field(default_factory=SecuritySummary)


@dataclass
class ComposeStack:
    name: str
    directory: Path
    compose_file: Path
    images: List[ComposeServiceImage] = field(default_factory=_compose_service_images_factory)
    load_error: Optional[str] = None

    @property
    def security_totals(self) -> SecuritySummary:
        totals = SecuritySummary(scanned=any(image.security.scanned for image in self.images))

        for image in self.images:
            totals.critical += image.security.critical
            totals.high += image.security.high
            totals.medium += image.security.medium
            totals.low += image.security.low
            totals.unspecified += image.security.unspecified

        return totals

    @property
    def update_count(self) -> int:
        return sum(1 for image in self.images if image.update_available is True)

    @property
    def container_states(self) -> Dict[str, int]:
        states: Dict[str, int] = {}

        for image in self.images:
            state = image.container_state or "unknown"
            states[state] = states.get(state, 0) + 1

        return states