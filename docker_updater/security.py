import re
from typing import Callable, Dict, Iterable, Optional, Set, Tuple

from docker_updater.docker_client import run_command
from docker_updater.models import SecuritySummary


CVE_PATTERN = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)

SEVERITY_KEYS: Dict[str, str] = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "unknown": "unspecified",
    "unspecified": "unspecified",
}


async def scan_image_security(
    image: str,
    log_callback: Optional[Callable[[str], None]] = None,
) -> SecuritySummary:
    result = await run_command(
        ["docker", "scout", "cves", f"local://{image}"],
        timeout=180.0,
        line_callback=log_callback,
        log_output=True,
    )

    if result.code != 0:
        error = result.stderr.strip() or result.stdout.strip() or "Docker Scout scan failed"

        return SecuritySummary(
            error=error,
            scanned=True,
        )

    return summarize_scout_text(result.stdout)


def summarize_scout_text(output: str) -> SecuritySummary:
    summary = SecuritySummary(scanned=True)
    seen: Set[Tuple[str, str]] = set()

    for cve_id, severity in find_cve_severities(output):
        key = (cve_id.upper(), severity)

        if key in seen:
            continue

        seen.add(key)
        apply_severity(summary, severity)

    return summary


def find_cve_severities(output: str) -> Iterable[Tuple[str, str]]:
    for line in output.splitlines():
        cve_match = CVE_PATTERN.search(line)

        if cve_match is None:
            continue

        severity = find_severity_in_line(line)

        if severity is None:
            continue

        yield cve_match.group(0), severity


def find_severity_in_line(line: str) -> Optional[str]:
    lower_line = line.lower()

    for raw_severity, normalized in SEVERITY_KEYS.items():
        if re.search(rf"\b{re.escape(raw_severity)}\b", lower_line):
            return normalized

    return None


def apply_severity(summary: SecuritySummary, severity: str) -> None:
    if severity == "critical":
        summary.critical += 1
    elif severity == "high":
        summary.high += 1
    elif severity == "medium":
        summary.medium += 1
    elif severity == "low":
        summary.low += 1
    else:
        summary.unspecified += 1