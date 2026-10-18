"""Running component commands: pin checks and a minimal, offline subprocess wrapper.

`ocs` talks to each component only through its public command, never by importing it. These
helpers are shared by every command that calls a component other than the demo (which keeps its
own, older copy of the same logic).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ocs.demo import ComponentProblem, installed_commit
from ocs.envelope import strip_controls
from ocs.lock import COMPONENTS

OUTPUT_LIMIT = 20_000
DEFAULT_TIMEOUT_SECONDS = 60.0


class ComponentRunError(Exception):
    """A component command could not be executed (timeout or OS error); maps to exit 4."""


@dataclass(frozen=True)
class ComponentRun:
    code: int
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)

    @property
    def crashed(self) -> bool:
        """An uncaught exception in the component, as opposed to a verdict it chose to give."""
        return "Traceback (most recent call last)" in self.stderr


def check_pins(
    commands: Iterable[str],
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
) -> Path:
    """Return the directory holding the named component commands, or raise ComponentProblem.

    Fails closed: a missing command, or a distribution not installed from its pinned commit,
    is a version problem (exit 2), never a pass.
    """
    directory = bin_dir or Path(sys.executable).parent
    problems: list[str] = []
    for command in commands:
        comp = COMPONENTS[command]
        exe = directory / comp.command
        if not (exe.is_file() and os.access(exe, os.X_OK)):
            problems.append(f"{comp.command} ({comp.distribution}) is not installed")
            continue
        found = commit_of(comp.distribution)
        if found != comp.sha:
            shown = found[:12] if found else "unknown source"
            problems.append(f"{comp.distribution} is at {shown}, expected {comp.sha[:12]}")
    if problems:
        raise ComponentProblem(
            "; ".join(problems)
            + ". Install with: pip install 'cognous-ocs[stack]' (from this repository)"
        )
    return directory


def clean_output(raw: bytes, limit: int | None = OUTPUT_LIMIT) -> str:
    text = raw.decode("utf-8", errors="replace")
    if limit is not None:
        text = text[:limit]
    return "".join(ch if ch in "\n\t" else strip_controls(ch) for ch in text)


def run_component(
    bin_dir: Path,
    command: str,
    args: list[str],
    *,
    cwd: Path | None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    home: Path | None = None,
    limit: int | None = OUTPUT_LIMIT,
) -> ComponentRun:
    """Run `command args` with a minimal environment; ComponentRunError on timeout or OS error."""
    env = {
        "PATH": str(bin_dir),
        "LANG": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(home or os.environ.get("HOME", "/nonexistent")),
    }
    try:
        proc = subprocess.run(  # noqa: S603 - fixed executable from the venv, no shell
            [str(bin_dir / command), *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComponentRunError(f"{command} could not run ({type(exc).__name__})") from exc
    return ComponentRun(
        proc.returncode, clean_output(proc.stdout, limit), clean_output(proc.stderr, limit)
    )
