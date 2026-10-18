"""Lock freshness: the `stack` extra pins equal ocs.lock, and any *.lock files agree.

The pins are the hub component-lock commits, except the control plane, which is ahead of
the hub lock (see provenance.json and docs/demo.md). A stale or edited pin
fails. Each extra needs requirements/<extra>.txt naming every dependency it declares (regenerate with
uv, see docs/testing.md); any requirements*.lock file must mention every pinned commit.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402


def _name(requirement: str) -> str:
    return re.sub(r"[-_.]+", "-", re.split(r"[\s<>=!~;\[@]", requirement, maxsplit=1)[0]).lower()


def check_requirement_locks(root: Path, extras: dict[str, list[str]]) -> list[str]:
    """requirements/<extra>.txt exists and names every dependency its extra declares."""
    errors: list[str] = []
    for extra, requirements in sorted(extras.items()):
        lock = root / "requirements" / f"{extra}.txt"
        if not lock.is_file():
            errors.append(f"requirements/{extra}.txt is missing")
            continue
        locked = {
            _name(m)
            for m in re.findall(
                r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:==| @ )", lock.read_text(encoding="utf-8"), re.M
            )
        }
        errors += [
            f"requirements/{extra}.txt does not lock {_name(r)}; regenerate it (docs/testing.md)"
            for r in requirements
            if _name(r) not in locked
        ]
    return errors


def check(root: Path) -> list[str]:
    sys.path.insert(0, str(root / "src"))
    from ocs.lock import COMPONENTS

    errors: list[str] = []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pins = project["optional-dependencies"]["stack"]
    want = {
        c.distribution: f"{c.distribution} @ git+https://github.com/{c.repository}@{c.sha}"
        for c in COMPONENTS.values()
    }
    have = {re.split(r"\s*@", p, maxsplit=1)[0]: p for p in pins}
    if set(have) != set(want):
        errors.append(f"stack extra distributions {sorted(have)} != lock {sorted(want)}")
    for dist, line in want.items():
        if have.get(dist) != line:
            errors.append(f"stack pin for {dist} differs from ocs.lock")
    for lock in sorted(root.glob("requirements*.lock")):
        text = lock.read_text(encoding="utf-8")
        errors += [
            f"{lock.name}: missing pinned commit for {c.distribution}"
            for c in COMPONENTS.values()
            if c.sha not in text
        ]
    errors += check_requirement_locks(root, project["optional-dependencies"])
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    errors = check(ap.parse_args(argv).root)
    print("\n".join(errors) if errors else "lock freshness: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
