import asyncio
import os
from pathlib import Path
from typing import Callable, Optional, Sequence

from docker_updater.models import CommandResult


def command_to_string(args: Sequence[str]) -> str:
    return " ".join(args)


def emit_output_lines(value: str, line_callback: Callable[[str], None]) -> None:
    for line in value.splitlines():
        if line.strip():
            line_callback(line)


async def run_command(
    args: Sequence[str],
    cwd: Optional[Path] = None,
    timeout: Optional[float] = 60.0,
    line_callback: Optional[Callable[[str], None]] = None,
    log_output: bool = False,
) -> CommandResult:
    if line_callback is not None:
        line_callback(f"$ {command_to_string(args)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        if line_callback is not None:
            line_callback(str(exc))

        return CommandResult(
            code=127,
            stdout="",
            stderr=str(exc),
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()

        message = f"Command timed out: {command_to_string(args)}"

        if line_callback is not None:
            line_callback(message)

        return CommandResult(
            code=124,
            stdout="",
            stderr=message,
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    code = process.returncode or 0

    if line_callback is not None:
        if log_output:
            emit_output_lines(stdout, line_callback)
            emit_output_lines(stderr, line_callback)
        elif code != 0:
            emit_output_lines(stderr or stdout, line_callback)

        if code != 0:
            line_callback(f"exit code: {code}")

    return CommandResult(
        code=code,
        stdout=stdout,
        stderr=stderr,
    )


async def run_streamed_command(
    args: Sequence[str],
    cwd: Path,
    line_callback: Callable[[str], None],
) -> int:
    line_callback(f"$ {command_to_string(args)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        line_callback(str(exc))
        return 127

    if process.stdout is not None:
        while True:
            line = await process.stdout.readline()

            if not line:
                break

            line_callback(line.decode("utf-8", errors="replace").rstrip())

    code = await process.wait()
    line_callback(f"exit code: {code}")

    return code