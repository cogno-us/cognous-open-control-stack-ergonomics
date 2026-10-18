"""The distribution and the only command are `cognous-ocs`; the import module is `cognous_ocs`.
No `ocs` console script and no `ocs` module ship. Offline: the built wheel installs into a fresh
venv with --no-index."""

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

from cognous_ocs import DISTRIBUTION, __version__
from cognous_ocs.cli import version_text

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_names_the_distribution_and_one_script() -> None:
    project = PYPROJECT["project"]
    assert project["name"] == DISTRIBUTION == "cognous-ocs"
    assert project["scripts"] == {"cognous-ocs": "cognous_ocs.cli:main"}
    assert PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/cognous_ocs"
    ]


def test_version_reports_the_command_name() -> None:
    assert version_text().splitlines()[0] == f"cognous-ocs {__version__}"


def test_docs_never_show_the_old_command() -> None:
    old = re.compile(r"(?<![\w./:-])ocs (?=[a-z-]+ |--)")
    for page in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        hits = [m.group(0) for m in old.finditer(page.read_text(encoding="utf-8"))]
        assert not hits, (page.name, hits)


def test_schema_snapshots_keep_their_original_urns() -> None:
    """Snapshots are immutable: their `$id` urns (`urn:ocs:...`) predate the rename and stay."""
    for path in sorted((ROOT / "schemas" / "v1").glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$id"].startswith("urn:ocs:schema:")


def _can_build() -> bool:
    return all(importlib.util.find_spec(m) for m in ("build", "hatchling"))


@pytest.mark.skipif(not _can_build(), reason="build and hatchling are dev dependencies")
def test_built_wheel_installs_offline_and_exposes_only_cognous_ocs(tmp_path: Path) -> None:
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
        names = zf.namelist()
    assert any(n.startswith("cognous_ocs/") for n in names)
    assert not any(n.split("/")[0] == "ocs" for n in names)
    assert "Name: cognous-ocs" in meta
    assert entry.splitlines()[1:] == ["cognous-ocs = cognous_ocs.cli:main"]
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

    bindir = venv / "bin"
    assert (bindir / "cognous-ocs").is_file()
    assert not (bindir / "ocs").exists()  # the only installed command is cognous-ocs

    def run(*cmd: str) -> tuple[int, str]:
        done = subprocess.run(list(cmd), capture_output=True, text=True, check=False)
        return done.returncode, done.stdout

    py = str(bindir / "python")
    for args in (("--version",), ("--format", "json", "doctor"), ("doctor",), ()):
        assert run(str(bindir / "cognous-ocs"), *args) == run(py, "-m", "cognous_ocs", *args), args
    code, out = run(str(bindir / "cognous-ocs"), "--version")
    assert code == 0 and out.startswith(f"cognous-ocs {__version__}\n")
    assert run(py, "-c", "import ocs")[0] != 0  # no generic top-level `ocs` module
    assert run(py, "-c", "import cognous_ocs")[0] == 0
    listed = subprocess.run(
        [py, "-m", "pip", "list", "--format=json"], capture_output=True, text=True, check=True
    )
    assert "cognous-ocs" in {p["name"] for p in json.loads(listed.stdout)}
