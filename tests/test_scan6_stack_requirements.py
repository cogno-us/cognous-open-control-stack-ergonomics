"""Scan 6 T1-8: requirements/stack.txt git pins must each be one strict line matching the lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from cognous_ocs.lock import COMPONENTS
from conftest import REPO_ROOT, load_script

lock = load_script("check_lock_freshness")
WANT = {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}
AAM = COMPONENTS["aam"]
BASE = f"{AAM.distribution} @ git+https://github.com/{AAM.repository}@{AAM.sha}"


@pytest.fixture
def stack_root(tmp_path: Path) -> Path:
    (tmp_path / "requirements").mkdir()
    return tmp_path


def run(root: Path, text: str) -> list[str]:
    (root / "requirements" / "stack.txt").write_text(text, encoding="utf-8")
    errors: list[str] = lock.check_stack_requirements(root, WANT)
    return errors


def real_text() -> str:
    return (REPO_ROOT / "requirements" / "stack.txt").read_text(encoding="utf-8")


def test_real_stack_txt_is_clean(stack_root: Path) -> None:
    assert run(stack_root, real_text()) == []


@pytest.mark.parametrize(
    "variant",
    [
        BASE + "#egg=agent-action-manifest",
        BASE + " ; sys_platform == 'linux'",
        BASE + "deadbeef",
        BASE + " --hash=sha256:" + "a" * 64,
        BASE + " \\",
        BASE + " extra",
        BASE + ".git",
        BASE.replace(AAM.sha, AAM.sha.upper()),
        BASE.replace("cogno-us/", "someone/"),
        BASE.replace(AAM.repository, "cogno-us/cognous-control-plane"),
        BASE.replace("https://", "http://"),
        "-e " + BASE,
    ],
)
def test_malformed_or_wrong_pin_line_fails(stack_root: Path, variant: str) -> None:
    errors = run(stack_root, real_text().replace(BASE, variant))
    assert errors, variant


def test_duplicate_component_line_fails(stack_root: Path) -> None:
    errors = run(stack_root, real_text() + BASE + "\n")
    assert any("2 times" in e for e in errors)


def test_duplicate_with_other_sha_fails(stack_root: Path) -> None:
    errors = run(stack_root, real_text() + BASE.replace(AAM.sha, "0" * 40) + "\n")
    assert errors


def test_other_repo_line_fails(stack_root: Path) -> None:
    text = real_text() + "evil @ git+https://github.com/someone/evil@" + "a" * 40 + "\n"
    assert any("strict" in e for e in run(stack_root, text))


def test_unlisted_cogno_us_component_fails(stack_root: Path) -> None:
    text = real_text() + "extra-dist @ git+https://github.com/cogno-us/extra@" + "a" * 40 + "\n"
    assert any("extra-dist" in e for e in run(stack_root, text))
