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


def _no_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate member {key!r}")
        out[key] = value
    return out


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


def _finite_float(lexeme: str) -> float:
    value = float(lexeme)
    if value in (float("inf"), float("-inf")):
        raise ValueError("float overflows to infinity")
    return value


def strict_json_loads(text: str) -> dict[str, Any]:
    """Parse one JSON object: no duplicate members, no NaN/Infinity/overflowing floats.

    Python's default parser keeps the last of several equal members, so a file that reads
    false at the top could be flipped by an appended duplicate. That is refused here.
    ValueError (JSONDecodeError included) on any refusal, and for a root that is not an object.
    """
    try:
        data = json.loads(
            text,
            object_pairs_hook=_no_duplicate_members,
            parse_constant=_refuse_constant,
            parse_float=_finite_float,
        )
    except RecursionError as exc:
        raise ValueError("nesting too deep") from exc
    if not isinstance(data, dict):
        raise ValueError("root is not a JSON object")
    return data


def read_release_state(root: Path) -> dict[str, Any]:
    """Parse release-state.json strictly. Missing or malformed raises StateError (= HOLD)."""
    path = root / "release-state.json"
    try:
        data = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"release-state.json unreadable ({type(exc).__name__})") from exc
    if set(data) != RELEASE_STATE_KEYS:
        raise StateError("release-state.json members differ from the closed set")
    for key in ("publication_authorized", "production_authorized"):
        if not isinstance(data[key], bool):
            raise StateError(f"release-state.json {key} must be a JSON boolean")
    return data


def on_hold(state: dict[str, Any]) -> bool:
    """True while publication or production is not authorized."""
    return not (state["publication_authorized"] and state["production_authorized"])
