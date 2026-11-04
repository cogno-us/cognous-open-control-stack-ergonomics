"""Parsing and integrity checks for the committed requirement locks. Standard library only.

Two kinds of lock file live in requirements/:

- `<name>.txt`      uv-generated, hashed, PyPI-only: every logical line is
                    `name==version [; marker]` followed by one or more `--hash=sha256:<64 hex>`.
- `<name>-vcs.txt`  the first-party components, one `dist @ git+https://github.com/<repo>@<40 hex>`
                    line each. pip cannot verify hashes of VCS requirements, so the full commit SHA
                    is the pin and the file is installed in its own `--no-deps` step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")
_ENTRY = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9._+!]+)"
    r"(?:\s*;\s*(?P<marker>[^#\\]+?))?"
    r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})*)\s*"
)
_COMMENT = re.compile(r"\s*#")
_SPEC = re.compile(r"\s*(==|>=|<=|!=|>|<|~=)\s*([0-9][0-9A-Za-z.+!]*)\s*")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def logical_lines(text: str) -> list[tuple[int, str, bool]]:
    """Join backslash continuations as pip does: (first line number, text, ends_open)."""
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


@dataclass
class Entry:
    name: str
    version: str
    marker: str | None
    hashes: list[str]
    line: int


@dataclass
class HashedLock:
    entries: list[Entry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def versions(self) -> dict[str, str]:
        return {canonical(e.name): e.version for e in self.entries}


def parse_hashed_lock(label: str, text: str) -> HashedLock:
    """Strictly parse a hashed PyPI lock; every violation is an entry in `.errors`."""
    lock = HashedLock()
    seen: dict[str, int] = {}
    owner_of_hash: dict[str, str] = {}
    for number, line, open_end in logical_lines(text):
        if not line.strip() or _COMMENT.match(line):
            continue
        match = None if open_end else _ENTRY.fullmatch(line)
        if match is None:
            token = (line.split() or ["?"])[0][:60]
            hint = ""
            if " @ " in line or "git+" in line:
                hint = " (VCS and URL requirements belong in the -vcs.txt file, pinned by SHA)"
            elif line.lstrip().startswith("-"):
                hint = " (options such as indexes, constraints and includes are not allowed)"
            lock.errors.append(
                f"{label} line {number} ({token}) is not a strict `name==version` pin "
                f"followed by sha256 hashes{hint}"
            )
            continue
        name = canonical(match["name"])
        hashes = HASH.findall(match["hashes"] or "")
        if not hashes:
            lock.errors.append(
                f"{label} line {number}: {name}=={match['version']} has no sha256 hash"
            )
        if len(set(hashes)) != len(hashes):
            lock.errors.append(f"{label} line {number}: {name} repeats a hash")
        for digest in hashes:
            if len(set(digest)) == 1:
                lock.errors.append(
                    f"{label} line {number}: {name} carries a placeholder hash {digest[:8]}..."
                )
            other = owner_of_hash.setdefault(digest, name)
            if other != name:
                lock.errors.append(f"{label} line {number}: {name} shares a hash with {other}")
        if name in seen:
            lock.errors.append(
                f"{label} line {number}: {name} is pinned twice (first on line {seen[name]})"
            )
        seen[name] = number
        lock.entries.append(Entry(name, match["version"], match["marker"], hashes, number))
    return lock


def release(version: str) -> tuple[int, ...] | None:
    head = re.match(r"[0-9]+(?:\.[0-9]+)*", version)
    if head is None:
        return None
    parts = [int(p) for p in head[0].split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def satisfies(version: str, specifier: str) -> bool | None:
    """Does `version` satisfy a comma-separated specifier? None if the specifier is unsupported."""
    have = release(version)
    if have is None:
        return None
    if not specifier.strip():
        return True
    for clause in specifier.split(","):
        m = _SPEC.fullmatch(clause)
        if m is None or m[1] == "~=":
            return None
        want = release(m[2])
        if want is None:
            return None
        ok = {
            "==": have == want,
            ">=": have >= want,
            "<=": have <= want,
            "!=": have != want,
            ">": have > want,
            "<": have < want,
        }[m[1]]
        if not ok:
            return False
    return True


def split_requirement(requirement: str) -> tuple[str, str, bool]:
    """(canonical name, specifier text, is_direct_reference) for a pyproject requirement string."""
    body = requirement.split(";", 1)[0].strip()
    direct = " @ " in body or "@" in body.split("[")[0]
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)", body)
    if m is None:
        return canonical(body), "", direct
    return canonical(m[1]), m[2].strip() if not direct else "", direct


def check_ranges(label: str, lock: HashedLock, requirements: list[str]) -> list[str]:
    """Every ranged requirement is locked at a version inside its range."""
    errors: list[str] = []
    versions = lock.versions()
    for requirement in requirements:
        name, specifier, direct = split_requirement(requirement)
        if direct:
            continue
        if name not in versions:
            errors.append(f"{label} does not lock {name}")
            continue
        verdict = satisfies(versions[name], specifier)
        if verdict is None:
            errors.append(
                f"{label}: cannot evaluate {requirement!r} against {name}=={versions[name]}"
            )
        elif not verdict:
            errors.append(f"{label}: {name}=={versions[name]} violates {requirement!r}")
    return errors
