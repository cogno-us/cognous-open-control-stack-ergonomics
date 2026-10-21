"""Lock freshness: the `stack` extra pins equal cognous_ocs.lock, and any *.lock files agree.

The pins are the current component mains, which differ from the hub component-lock commits
until the hub lock is updated (see provenance.json and docs/demo.md). A stale or edited pin
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


GIT_PIN = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*) @ git\+https://github\.com/(cogno-us/[A-Za-z0-9._-]+)@([0-9a-f]{40})"
)
_INDEXED_PIN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9._+!]+(?:\s+--hash=sha256:[0-9a-f]{64})*\s*"
)
_COMMENT = re.compile(r"\s*#")


def _logical_lines(text: str) -> list[tuple[int, str, bool]]:
    """Join backslash continuations as pip does: (first line number, logical text, ends_open).

    A comment line is never joined and ends a pending continuation; a continuation that reaches
    a comment line or the end of the file is reported open so the caller refuses it.
    """
    out: list[tuple[int, str, bool]] = []
    parts: list[str] = []
    first = 0
    for number, line in enumerate(text.splitlines(), 1):
        if _COMMENT.match(line):
            if parts:
                out.append((first, "".join(parts), True))
                parts = []
            out.append((number, line, False))
        elif line.endswith("\\"):
            if not parts:
                first = number
            parts.append(line.strip("\\"))
        elif parts:
            parts.append(line)
            out.append((first, "".join(parts), False))
            parts = []
        else:
            out.append((number, line, False))
    if parts:
        out.append((first, "".join(parts), True))
    return out


def check_stack_requirements(root: Path, want: dict[str, tuple[str, str]]) -> list[str]:
    """requirements/stack.txt carries exactly the lock's git pins, one strict line each.

    Allow-list: after joining backslash continuations as pip does, every logical line must be blank, a
    comment, exactly `<dist> @ git+https://github.com/cogno-us/<repo>@<40 lowercase hex>`, or an indexed
    `name==version` pin optionally followed by `--hash=sha256:<64 hex>` options (the uv-generated form).
    Anything else (URLs, paths, other VCS schemes, environment references, options, indented
    non-comment lines, a trailing backslash) is refused. Each lock component appears exactly once with
    the lock's repository and sha.
    """
    path = root / "requirements" / "stack.txt"
    if not path.is_file():
        return []  # check_requirement_locks reports the missing file
    errors: list[str] = []
    seen: dict[str, list[tuple[str, str]]] = {}
    for number, line, open_end in _logical_lines(path.read_text(encoding="utf-8")):
        if not line.strip() or _COMMENT.match(line):
            continue
        match = None if open_end else GIT_PIN.fullmatch(line)
        if match:
            seen.setdefault(match[1], []).append((match[2], match[3]))
        elif open_end or not _INDEXED_PIN.fullmatch(line):
            token = (line.split() or ["?"])[0][:60]
            errors.append(
                f"requirements/stack.txt line {number} ({token}) is not a strict "
                "`<dist> @ git+https://github.com/cogno-us/<repo>@<40 hex>` pin or `name==version` pin"
            )
    for dist, pin in want.items():
        pins = seen.get(dist, [])
        if len(pins) > 1:
            errors.append(
                f"requirements/stack.txt pins {dist} {len(pins)} times; exactly one pin is allowed"
            )
        if pins != [pin]:
            errors.append(f"requirements/stack.txt pin for {dist} differs from cognous_ocs.lock")
    listed = {c[1] for c in want.values()}
    errors += [
        f"requirements/stack.txt names commit {sha[:12]} that cognous_ocs.lock does not list"
        for sha in sorted({p[1] for pins in seen.values() for p in pins} - listed)
    ]
    errors += [
        f"requirements/stack.txt has a git pin for {dist} that cognous_ocs.lock does not list"
        for dist in sorted(set(seen) - set(want))
    ]
    return errors


def check(root: Path) -> list[str]:
    sys.path.insert(0, str(root / "src"))
    from cognous_ocs.lock import COMPONENTS

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
            errors.append(f"stack pin for {dist} differs from cognous_ocs.lock")
    for lock in sorted(root.glob("requirements*.lock")):
        text = lock.read_text(encoding="utf-8")
        errors += [
            f"{lock.name}: missing pinned commit for {c.distribution}"
            for c in COMPONENTS.values()
            if c.sha not in text
        ]
    errors += check_stack_requirements(
        root, {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}
    )
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
