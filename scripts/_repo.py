"""Shared helpers for the repository check scripts. Standard library only; no network."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
RELEASE_STATE_KEYS = {"schema", "publication_authorized", "production_authorized", "basis", "note"}


class StateError(Exception):
    """release-state.json is missing or malformed; callers treat that as HOLD."""


def tracked_files(root: Path) -> list[Path]:
    """Files under root that git tracks; falls back to a directory walk outside a work tree."""
    try:
        out = subprocess.run(  # noqa: S603
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],  # noqa: S607
            capture_output=True,
            check=True,
        ).stdout
        names = [n for n in out.decode("utf-8").split("\0") if n]
        return sorted(root / n for n in names if (root / n).is_file())
    except (OSError, subprocess.CalledProcessError):
        found: list[Path] = []
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            found += [Path(base) / n for n in names]
        return sorted(found)


def read_release_state(root: Path) -> dict[str, Any]:
    """Parse release-state.json strictly. Missing or malformed raises StateError (= HOLD)."""
    path = root / "release-state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"release-state.json unreadable ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or set(data) != RELEASE_STATE_KEYS:
        raise StateError("release-state.json members differ from the closed set")
    for key in ("publication_authorized", "production_authorized"):
        if not isinstance(data[key], bool):
            raise StateError(f"release-state.json {key} must be a JSON boolean")
    return data


def on_hold(state: dict[str, Any]) -> bool:
    """True while publication or production is not authorized."""
    return not (state["publication_authorized"] and state["production_authorized"])
