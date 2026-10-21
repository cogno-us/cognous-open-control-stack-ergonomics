"""Scan 5 C-T2-2: requirements/stack.txt must carry exactly the lock's component pins."""

from __future__ import annotations

from pathlib import Path

import pytest

from cognous_ocs.lock import COMPONENTS
from conftest import REPO_ROOT, load_script

lock = load_script("check_lock_freshness")


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    (root / "requirements").mkdir(parents=True)
    (root / "src").mkdir()
    for name in ("pyproject.toml",):
        (root / name).write_bytes((REPO_ROOT / name).read_bytes())
    (root / "requirements" / "stack.txt").write_bytes(
        (REPO_ROOT / "requirements" / "stack.txt").read_bytes()
    )
    return root


def stack(root: Path) -> Path:
    return root / "requirements" / "stack.txt"


def test_repository_stack_requirements_match_the_lock() -> None:
    assert lock.check(REPO_ROOT) == []


def test_zeroed_sha_in_stack_txt_fails(repo_copy: Path) -> None:
    sha = COMPONENTS["aam"].sha
    stack(repo_copy).write_text(stack(repo_copy).read_text().replace(sha, "0" * 40))
    errors = lock.check_stack_requirements(
        repo_copy, {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}
    )
    assert any("agent-action-manifest differs" in e for e in errors)
    assert any("000000000000" in e for e in errors)


def test_extra_git_pin_in_stack_txt_fails(repo_copy: Path) -> None:
    stack(repo_copy).write_text(
        stack(repo_copy).read_text()
        + "evil-extra @ git+https://github.com/someone/evil@"
        + "a" * 40
        + "\n"
    )
    errors = lock.check_stack_requirements(
        repo_copy, {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}
    )
    assert any("evil-extra" in e for e in errors)


def test_dropped_component_pin_fails(repo_copy: Path) -> None:
    lines = [
        x
        for x in stack(repo_copy).read_text().splitlines()
        if not x.startswith("agent-replay-bundle")
    ]
    stack(repo_copy).write_text("\n".join(lines) + "\n")
    errors = lock.check_stack_requirements(
        repo_copy, {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}
    )
    assert any("agent-replay-bundle" in e for e in errors)
