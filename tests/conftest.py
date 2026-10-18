from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ocs.lock import COMPONENTS


def stack_installed() -> bool:
    bin_dir = Path(sys.executable).parent
    return all((bin_dir / c.command).is_file() for c in COMPONENTS.values())


@pytest.fixture
def stack() -> None:
    """The real pinned components. CI sets OCS_REQUIRE_STACK=1 so a missing stack fails."""
    if not stack_installed():
        if os.environ.get("OCS_REQUIRE_STACK") == "1":
            pytest.fail("OCS_REQUIRE_STACK=1 but the component commands are not installed")
        pytest.skip("component commands not installed (pip install '.[stack]')")


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.chdir(work)
    return work
