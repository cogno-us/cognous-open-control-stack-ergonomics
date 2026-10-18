"""Composite action and workflow hygiene, checked by code (cli-contract section 9).

- Every third-party `uses:` is pinned by a 40-character commit SHA with its release tag in a
  comment on the same line (local `./` references are exempt).
- Workflows pin the runner image: no `*-latest` labels.
- The action's shell steps contain no `${{ }}` expressions: inputs reach scripts through `env:`.
- The action is composite, declares exactly the documented inputs, and every one of them is
  named in docs/action.md.
- The minimal upload names files and the two report directories, never the whole output
  directory; retention defaults to 14 days; the full upload defaults to off.
- release-version.txt equals the package version (ocs.__version__ and pyproject.toml).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402

ACTION_DIR = ".github/actions/ocs"
EXPECTED_INPUTS = {
    "manifest",
    "workflow",
    "evidence-pack",
    "non-assurance",
    "strict",
    "out-dir",
    "report-mode",
    "upload-reports",
    "upload-full-artifacts",
    "retention-days",
    "upload-name",
    "install",
}
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
TAG_COMMENT = re.compile(r"^\s*#\s*v\d+(?:\.\d+){0,2}\S*\s*$")
RUNNER_LATEST = re.compile(r"^\s*runs-on:\s*\S*-latest\b", re.M)
MINIMAL_PATH_END = (
    "ci-summary.json",
    "ci-summary.md",
    "ci-diagnostics.json",
    "evidence/",
    "reports/",
)


def yaml_files(root: Path) -> list[Path]:
    found = sorted((root / ".github").rglob("*.yml")) + sorted((root / ".github").rglob("*.yaml"))
    return [p for p in found if p.is_file()]


def check_pins(root: Path) -> list[str]:
    errors: list[str] = []
    for path in yaml_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), start=1):
            m = USES.match(line)
            if not m or m["ref"].startswith(("./", "docker://")):
                continue
            if not PINNED.match(m["ref"]):
                errors.append(f"{rel}:{n}: `uses: {m['ref']}` is not pinned by a 40-hex commit SHA")
            elif not TAG_COMMENT.match(m["rest"]):
                errors.append(f"{rel}:{n}: pinned action lacks its release tag in a comment")
        if RUNNER_LATEST.search(text):
            errors.append(f"{rel}: runner image must be pinned, not a *-latest label")
    return errors


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a mapping")
    return data


def check_action(root: Path) -> list[str]:
    errors: list[str] = []
    base = root / ACTION_DIR
    try:
        action = _load(base / "action.yml")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"{ACTION_DIR}/action.yml unreadable ({type(exc).__name__})"]
    if action.get("runs", {}).get("using") != "composite":
        errors.append("action is not a composite action")
    inputs = set(action.get("inputs", {}))
    if inputs != EXPECTED_INPUTS:
        errors.append(
            f"action inputs differ from the documented set: {sorted(inputs ^ EXPECTED_INPUTS)}"
        )
    defaults = {k: str(v.get("default")) for k, v in action.get("inputs", {}).items()}
    for key, want in (
        ("retention-days", "14"),
        ("upload-full-artifacts", "false"),
        ("non-assurance", "false"),
    ):
        if defaults.get(key) != want:
            errors.append(f"input {key} must default to {want!r}")
    steps = action.get("runs", {}).get("steps", [])
    for step in steps:
        if "run" in step and "${{" in step["run"]:
            errors.append(
                f"step {step.get('id', '?')}: a run script contains ${{{{ }}}}; pass it through env"
            )
        if "run" in step and step.get("shell") != "bash":
            errors.append(f"step {step.get('id', '?')}: shell must be bash")
    minimal = next((s for s in steps if s.get("id") == "upload-reports"), None)
    if minimal is None:
        errors.append("no upload-reports step")
    else:
        lines = [x.strip() for x in str(minimal["with"]["path"]).splitlines() if x.strip()]
        if not lines or not all(x.endswith(MINIMAL_PATH_END) for x in lines):
            errors.append(
                "the minimal upload must name only summaries, diagnostics, evidence/ and reports/"
            )
    docs = root / "docs" / "action.md"
    doc_text = docs.read_text(encoding="utf-8") if docs.is_file() else ""
    for name in sorted(inputs):
        if f"`{name}`" not in doc_text:
            errors.append(f"docs/action.md does not document input `{name}`")
    return errors


def check_version(root: Path) -> list[str]:
    sys.path.insert(0, str(root / "src"))
    from ocs import __version__

    try:
        released = (root / ACTION_DIR / "release-version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ["release-version.txt is missing"]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    errors = []
    if released != __version__:
        errors.append(f"release-version.txt {released!r} != ocs.__version__ {__version__!r}")
    if released != project:
        errors.append(f"release-version.txt {released!r} != pyproject version {project!r}")
    return errors


def run(root: Path) -> list[str]:
    return check_pins(root) + check_action(root) + check_version(root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    errors = run(ap.parse_args(argv).root)
    print("\n".join(errors) if errors else "action and workflow hygiene: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
