from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from cognous_ocs.lock import COMPONENTS


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


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str) -> Any:
    """Import scripts/<name>.py as a module (scripts is not a package)."""
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        f"script_{name}", REPO_ROOT / "scripts" / f"{name}.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the repository's checked files, safe to mutate."""
    dest = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT,
        dest,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "dist", "build", "*.egg-info", "__pycache__", ".*_cache", ".coverage"
        ),
    )
    return dest
