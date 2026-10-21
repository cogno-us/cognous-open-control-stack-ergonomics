"""Claim-boundary checker (cli-contract section 10).

Fails if any tracked document, README, action text, source string or built wheel says
certified, compliant or production-ready (and close variants) while publication or
production is on HOLD per release-state.json. A missing or malformed release-state.json
counts as HOLD. Matches are excused only by an exact entry in
scripts/claim_boundaries_allowlist.json; an allow-list entry that no longer matches
anything is itself an error, so the list cannot rot.

Usage: check_claim_boundaries.py [--root DIR] [--wheel FILE ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402

ALLOWLIST_NAME = "scripts/claim_boundaries_allowlist.json"
SEP = r"[\s\-_]*"
RULES: dict[str, re.Pattern[str]] = {
    "certified": re.compile(r"\bcertif(?:ied|ies|ying|y|ication)\b"),
    "compliant": re.compile(r"\bcompl(?:iant|iance|y|ies|ied|ying)\b"),
    "production-ready": re.compile(
        rf"\b(?:production|prod|enterprise|audit){SEP}(?:ready|grade)\b"
        rf"|\bready{SEP}for{SEP}production\b"
    ),
}
IGNORABLE = {"Cf"}  # zero-width and soft-hyphen characters are dropped before matching
TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".yml", ".yaml", ".toml", ".html", ".cfg", ".sh"}
TEXT_NAMES = {"Makefile", "pre-push", "NOTICE"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str  # the original line, stripped

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.text[:160]}"


def _normalise(text: str) -> tuple[str, list[int]]:
    """NFKC, casefold, drop ignorable characters; return text and each char's source line."""
    chars: list[str] = []
    lines: list[int] = []
    line = 1
    for raw in unicodedata.normalize("NFKC", text):
        if raw == "\n":
            line += 1
        if unicodedata.category(raw) in IGNORABLE:
            continue
        for ch in raw.casefold():
            chars.append(ch)
            lines.append(line)
    return "".join(chars), lines


def scan_text(path: str, text: str) -> list[Finding]:
    """All rule matches in text. Phrases split across lines or by hyphen variants still match."""
    norm, line_of = _normalise(text.replace("‐", "-").replace("‑", "-"))
    norm = norm.replace("‒", "-").replace("–", "-").replace("—", "-")
    source_lines = unicodedata.normalize("NFKC", text).split("\n")
    found: list[Finding] = []
    for rule, pattern in RULES.items():
        for m in pattern.finditer(norm):
            ln = line_of[m.start()] if m.start() < len(line_of) else len(source_lines)
            raw = source_lines[ln - 1] if ln - 1 < len(source_lines) else ""
            found.append(Finding(path, ln, rule, raw.strip()))
    return sorted(set(found), key=lambda f: (f.path, f.line, f.rule))


def load_allowlist(root: Path) -> dict[str, Any]:
    data = _repo.strict_json_loads((root / ALLOWLIST_NAME).read_text(encoding="utf-8"))
    if set(data) != {"excluded_paths", "allowed"}:
        raise ValueError("allow-list members differ from the closed set")
    for entry in data["excluded_paths"]:
        if set(entry) != {"path", "reason"} or not entry["reason"].strip():
            raise ValueError("excluded_paths entries need path and reason")
    for entry in data["allowed"]:
        if set(entry) != {"path", "rule", "line", "reason"} or not entry["reason"].strip():
            raise ValueError("allowed entries need path, rule, line and reason")
        if entry["rule"] not in RULES:
            raise ValueError(f"allowed entry names unknown rule {entry['rule']!r}")
    return data


def _excluded(rel: str, excluded: list[dict[str, str]]) -> bool:
    return any(
        rel == e["path"] or (e["path"].endswith("/") and rel.startswith(e["path"]))
        for e in excluded
    )


def _is_text(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES


def scan_tree(root: Path, allow: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for path in _repo.tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if _excluded(rel, allow["excluded_paths"]) or not _is_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(
                Finding(rel, 0, "undecodable", "text file is not UTF-8; cannot be checked")
            )
            continue
        findings += scan_text(rel, text)
    return findings


def scan_wheel(wheel: Path) -> list[Finding]:
    """Scan the text members of a built wheel.

    METADATA is header fields plus the README as the long description. The README part is
    reported under the path README.md so the same allow-list entries apply to what ships.
    """
    findings: list[Finding] = []
    with zipfile.ZipFile(wheel) as zf:
        for name in sorted(zf.namelist()):
            base = name.rsplit("/", 1)[-1]
            if base in {"LICENSE", "NOTICE"} or not (_is_text(Path(name)) or base == "METADATA"):
                continue
            text = zf.read(name).decode("utf-8", "replace")
            if base == "METADATA":
                header, _, body = text.partition("\n\n")
                findings += scan_text(f"{wheel.name}!{name}#header", header)
                findings += scan_text("README.md", body)
            else:
                findings += scan_text(f"{wheel.name}!{name}", text)
    return findings


def apply_allowlist(
    findings: list[Finding], allow: dict[str, Any]
) -> tuple[list[Finding], list[dict[str, str]]]:
    """Return (unexcused findings, allow-list entries that matched nothing)."""
    used: set[int] = set()
    left: list[Finding] = []
    entries = allow["allowed"]
    for f in findings:
        hit = [
            i
            for i, e in enumerate(entries)
            if e["path"] == f.path and e["rule"] == f.rule and e["line"] == f.text
        ]
        if hit:
            used.update(hit)
        else:
            left.append(f)
    return left, [e for i, e in enumerate(entries) if i not in used]


def run(root: Path, wheels: list[Path]) -> tuple[int, list[str]]:
    msgs: list[str] = []
    try:
        state = _repo.read_release_state(root)
    except _repo.StateError as exc:
        msgs.append(f"HOLD assumed: {exc}")
        state = {"publication_authorized": False, "production_authorized": False}
    if not _repo.on_hold(state):
        msgs.append("publication and production are both authorized: claim rules not enforced")
        return 0, msgs
    allow = load_allowlist(root)
    findings = scan_tree(root, allow)
    for wheel in wheels:
        findings += scan_wheel(wheel)
    left, stale = apply_allowlist(findings, allow)
    for f in left:
        msgs.append(f"claim boundary: {f.render()}")
    for e in stale:
        msgs.append(
            f"stale allow-list entry (matches nothing): {e['path']} [{e['rule']}] {e['line'][:80]}"
        )
    if left or stale:
        return 1, msgs
    msgs.append(f"claim boundary: ok (HOLD enforced; {len(findings)} excused matches)")
    return 0, msgs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    ap.add_argument("--wheel", type=Path, action="append", default=[])
    args = ap.parse_args(argv)
    code, msgs = run(args.root, args.wheel)
    print("\n".join(msgs))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
