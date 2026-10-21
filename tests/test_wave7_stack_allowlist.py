"""Wave 7 T1-B: requirements/stack.txt is an allow-list; continuations are joined as pip does."""

from __future__ import annotations

from pathlib import Path

import pytest

from cognous_ocs.lock import COMPONENTS
from conftest import REPO_ROOT, load_script

lock = load_script("check_lock_freshness")
WANT = {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}
HEX = "a" * 40


def run(tmp_path: Path, extra: str) -> list[str]:
    (tmp_path / "requirements").mkdir(exist_ok=True)
    text = (REPO_ROOT / "requirements" / "stack.txt").read_text(encoding="utf-8") + extra
    (tmp_path / "requirements" / "stack.txt").write_text(text, encoding="utf-8")
    errors: list[str] = lock.check_stack_requirements(tmp_path, WANT)
    return errors


@pytest.mark.parametrize(
    "extra",
    [
        f"evil-dist \\\n    @ git+https://github.com/someone/evil@{HEX}\n",
        "evil[x] @https://evil.example/x.whl\n",
        "https://evil.example/e.whl\n",
        "./vendor/evil\n",
        "file:///tmp/evil\n",
        "hg+https://evil.example/r\n",
        "svn+https://evil.example/r\n",
        "evil-1.0-py3-none-any.whl\n",
        "${EVIL}\n",
        "evil==1.0 --extra-index-url https://evil.example\n",
        "    evil==1.0\n",
        "evil==1.0 \\\n",
        "evil==1.0 \\\n    # not a join\n",
        "evil==1.0 \\\n    @ https://evil.example/x.whl\n",
    ],
)
def test_non_allow_listed_logical_line_fails(tmp_path: Path, extra: str) -> None:
    assert any("strict" in e for e in run(tmp_path, extra)), extra


def test_trailing_backslash_at_eof_fails(tmp_path: Path) -> None:
    assert any("strict" in e for e in run(tmp_path, "evil==1.0 \\"))


def test_real_file_and_plain_indexed_pin_pass(tmp_path: Path) -> None:
    assert run(tmp_path, "") == []
    assert run(tmp_path, "extra-dist==1.2.3\n# a comment\n\n    # indented comment\n") == []
