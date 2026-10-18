"""Guards on the repository's own test defaults, so loosening one is a visible change."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from ocs.lock import COMPONENTS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_network_is_disabled_and_the_coverage_floor_is_80(pyproject: dict[str, Any]) -> None:
    ini = pyproject["tool"]["pytest"]["ini_options"]
    assert "--disable-socket" in ini["addopts"]
    assert "--cov-fail-under=80" in ini["addopts"]
    assert "--strict-markers" in ini["addopts"]
    assert ini["filterwarnings"] == ["error"]


def test_typing_is_strict_and_ruff_includes_the_security_rules(pyproject: dict[str, Any]) -> None:
    assert pyproject["tool"]["mypy"]["strict"] is True
    assert "S" in pyproject["tool"]["ruff"]["lint"]["select"]


def test_every_extra_has_a_lock_file(pyproject: dict[str, Any]) -> None:
    for extra in pyproject["project"]["optional-dependencies"]:
        lock = ROOT / "requirements" / f"{extra}.txt"
        assert lock.is_file(), (
            f"requirements/{extra}.txt is missing; regenerate it (docs/testing.md)"
        )


def test_stack_lock_pins_the_same_commits_as_ocs_lock() -> None:
    text = (ROOT / "requirements" / "stack.txt").read_text(encoding="utf-8")
    found = set(re.findall(r"@ git\+https://github\.com/[^@\s]+@([0-9a-f]{40})", text))
    assert found == {c.sha for c in COMPONENTS.values()}


@pytest.mark.parametrize("extra", ["dev", "stack"])
def test_locked_packages_are_pinned_exactly_with_hashes(extra: str) -> None:
    text = (ROOT / "requirements" / f"{extra}.txt").read_text(encoding="utf-8")
    entries = re.split(r"\n(?=\S)", text)
    pinned = [e for e in entries if re.match(r"[A-Za-z0-9_.\-]+==", e)]
    assert pinned
    for entry in pinned:
        assert "--hash=sha256:" in entry, entry.splitlines()[0]
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            assert "==" in line or " @ git+https://" in line, f"unpinned requirement: {line}"
