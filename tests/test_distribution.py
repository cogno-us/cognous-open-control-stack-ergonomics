"""The distribution is `cognous-ocs`; the command stays `ocs` (plus the alias script), the import
module stays `ocs`. Offline: the built wheel installs into a fresh venv with --no-index."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from ocs import DISTRIBUTION, __version__
from ocs.cli import version_text

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_names_the_distribution_and_both_scripts() -> None:
    project = PYPROJECT["project"]
    assert project["name"] == DISTRIBUTION == "cognous-ocs"
    assert project["scripts"] == {"ocs": "ocs.cli:main", "cognous-ocs": "ocs.cli:main"}
    assert PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/ocs"]


def test_version_reports_command_and_distribution() -> None:
    lines = version_text().splitlines()
    assert lines[0] == f"ocs {__version__}"  # the command name stays first
    assert lines[1] == f"distribution {DISTRIBUTION}"


def test_text_never_installs_the_old_distribution_name() -> None:
    for rel in ("README.md", "docs/demo.md", "docs/development.md", "docs/cli-contract.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"pip install ['\"]?ocs\b", text), rel


def test_schema_snapshot_ids_do_not_embed_the_distribution() -> None:
    """Snapshots are immutable; their `$id` urn names the tool (`urn:ocs:...`), not the package."""
    for path in sorted((ROOT / "schemas" / "v1").glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$id"].startswith("urn:ocs:schema:")
        assert "cognous" not in path.read_text(encoding="utf-8")


def _can_build() -> bool:
    return all(importlib.util.find_spec(m) for m in ("build", "hatchling"))


@pytest.mark.skipif(not _can_build(), reason="build and hatchling are dev dependencies")
def test_built_wheel_installs_offline_and_both_scripts_agree(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    built = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist), str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-1500:]
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    assert [w.name for w in wheels] == [f"cognous_ocs-{__version__}-py3-none-any.whl"]
    assert [s.name for s in sdists] == [f"cognous_ocs-{__version__}.tar.gz"]
    with zipfile.ZipFile(wheels[0]) as zf:
        meta = zf.read(f"cognous_ocs-{__version__}.dist-info/METADATA").decode()
        entry = zf.read(f"cognous_ocs-{__version__}.dist-info/entry_points.txt").decode()
        assert any(n.startswith("ocs/") for n in zf.namelist())
    assert "Name: cognous-ocs" in meta
    assert "ocs = ocs.cli:main" in entry.splitlines()
    assert "cognous-ocs = ocs.cli:main" in entry.splitlines()
    with tarfile.open(sdists[0]) as tf:
        assert f"cognous_ocs-{__version__}/pyproject.toml" in tf.getnames()

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
    installed = subprocess.run(
        [str(venv / "bin" / "python"), "-m", "pip", "install", "--no-index", str(wheels[0])],
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr[-1500:]

    def run(script: str, *args: str) -> tuple[int, str]:
        done = subprocess.run(
            [str(venv / "bin" / script), *args], capture_output=True, text=True, check=False
        )
        return done.returncode, done.stdout

    for args in (("--version",), ("--format", "json", "doctor"), ("doctor",), ()):
        left, right = run("ocs", *args), run("cognous-ocs", *args)
        assert left == right, args
    code, out = run("cognous-ocs", "--version")
    assert code == 0 and out.startswith(f"ocs {__version__}\ndistribution cognous-ocs\n")
    listed = subprocess.run(
        [str(venv / "bin" / "python"), "-m", "pip", "list", "--format=json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "cognous-ocs" in {p["name"] for p in json.loads(listed.stdout)}
