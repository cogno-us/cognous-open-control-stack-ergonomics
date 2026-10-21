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
import html
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
        rf"\b(?:production|prod|deployment|deploy|enterprise|audit){SEP}(?:ready|grade|quality|worthy)\b"
        rf"|\bready{SEP}for{SEP}(?:production|prod|deployment)\b"
        rf"|\b(?:safe|fit|approved|cleared|suitable){SEP}for{SEP}(?:production|prod|deployment)\b"
    ),
    "attested": re.compile(
        rf"\bsoc{SEP}[123]\b(?:{SEP}type{SEP}(?:i{{1,2}}|[12]))?{SEP}(?:attest|audit)"
    ),
}
# Characters that hide a phrase: zero-width and format characters (Cf), variation selectors and
# other combining marks (Mn, after decomposition), and the markup that splits words in Markdown
# and HTML. They are dropped before matching.
IGNORABLE = {"Cf", "Mn"}
# A tag is stripped only when its name is a known HTML (or SVG/MathML) element, or a custom
# element name (contains a hyphen), and it stays on one line. Prose such as "if a<b ... c>d" is
# never swallowed as a tag; and scan_text also matches on the text with no tag stripped at all.
HTML_ELEMENTS = frozenset(
    """a abbr acronym address area article aside audio b base bdi bdo big blockquote body br
    button canvas caption center cite code col colgroup data datalist dd del details dfn dialog
    dir div dl dt em embed fieldset figcaption figure font footer form frame frameset h1 h2 h3 h4
    h5 h6 head header hgroup hr html i iframe img input ins kbd label legend li link main map
    mark marquee menu meta meter nav noscript object ol optgroup option output p param picture
    pre progress q rp rt ruby s samp script search section select slot small source span strike
    strong style sub summary sup table tbody td template textarea tfoot th thead time title tr
    track tt u ul var video wbr svg g path circle rect line polyline polygon ellipse text tspan
    defs use symbol math mi mn mo ms mtext mrow mfrac msup msub""".split()
)
TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9._]+)*)(?=[\s/>])[^<>\n]*>")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
MARKDOWN_MARKS = re.compile(r"[*`~\\]")
ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
# Latin lookalikes from Cyrillic, Greek, Armenian and Latin-extended letters, applied after
# NFKC and casefold. The sibling verifier repository's copy of this script uses the same table.
LOOKALIKES = str.maketrans(
    {
        # Cyrillic
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
        "һ": "h",
        "ԛ": "q",
        "ԝ": "w",
        "ѵ": "v",
        "к": "k",
        "м": "m",
        "н": "h",
        "т": "t",
        "в": "b",
        "ь": "b",
        "г": "r",
        "п": "n",
        "ү": "y",
        # Greek
        "ο": "o",
        "α": "a",
        "ν": "v",
        "ρ": "p",
        "ι": "i",
        "τ": "t",
        "κ": "k",
        "υ": "u",
        "ε": "e",
        "η": "n",
        "χ": "x",
        "γ": "y",
        "ϲ": "c",
        "ϳ": "j",
        "ς": "c",
        "β": "b",
        # Armenian
        "ս": "u",
        "օ": "o",
        "ո": "n",
        "ց": "g",
        "հ": "h",
        "ք": "p",
        "ԍ": "g",
        # Latin extended and IPA
        "ı": "i",
        "ɡ": "g",
        "ɑ": "a",
        "ɩ": "i",
        "ʀ": "r",
        "ɴ": "n",
        "ᴅ": "d",
        "ᴇ": "e",
        "ᴏ": "o",
        "ᴘ": "p",
        "ᴛ": "t",
        "ᴜ": "u",
        "ᴠ": "v",
        "ᴡ": "w",
        "ᴢ": "z",
        # small capitals and other IPA letters (scan 5)
        "ᴀ": "a",
        "ʙ": "b",
        "ᴄ": "c",
        "ꜰ": "f",
        "ɢ": "g",
        "ʜ": "h",
        "ɪ": "i",
        "ᴊ": "j",
        "ᴋ": "k",
        "ʟ": "l",
        "ᴍ": "m",
        "ꜱ": "s",
        "ʏ": "y",
        "ɛ": "e",
        "ɔ": "o",
    }
)
TEXT_SUFFIXES = {
    ".md", ".markdown", ".rst", ".adoc", ".txt", ".py", ".pyi", ".json", ".jsonl", ".yml", ".yaml",
    ".toml", ".html", ".htm", ".xml", ".svg", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".css", ".csv", ".tsv", ".cfg", ".ini", ".sh", ".bash", ".tex", ".ipynb", ".lock", ".in",
    ".properties", ".env", ".conf",
}  # fmt: skip
TEXT_NAMES = {"Makefile", "pre-push", "NOTICE"}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".zip", ".gz", ".tgz",
    ".bz2", ".xz", ".whl", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pyc", ".so", ".db",
    ".sqlite", ".mp4", ".mov",
}  # fmt: skip


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str  # the original line, stripped

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.text[:160]}"


def _fold_entities(text: str) -> str:
    """Decode HTML entities; a decoded line break becomes a space so line numbers hold."""

    def one(m: re.Match[str]) -> str:
        out = html.unescape(m.group(0))
        return " " if any(c in out for c in "\r\n\x0b\x0c\x85\u2028\u2029") else out

    return ENTITY.sub(one, text)


def _normalise(text: str, *, strip_tags: bool = True) -> tuple[str, list[int]]:
    """Fold text for matching; return it and each char's source line.

    Order: entities, NFKC, decompose, casefold, drop ignorable characters, unify dashes and
    minus signs, fold Latin lookalikes, drop Markdown and HTML markup. With strip_tags false
    only comments and Markdown marks are dropped, so text between a "<" and a later ">" survives.
    """
    chars: list[str] = []
    lines: list[int] = []
    line = 1
    for raw in unicodedata.normalize("NFKC", _fold_entities(text)):
        if raw == "\n":
            line += 1
        for part in unicodedata.normalize("NFD", raw):
            if unicodedata.category(part) in IGNORABLE:
                continue
            for ch in part.casefold():
                if unicodedata.category(ch) == "Pd" or ch in "\u2212\u2043\ufe63\u02d7":
                    ch = "-"
                chars.append(ch.translate(LOOKALIKES))
                lines.append(line)
    return _drop_markup("".join(chars), lines, strip_tags)


def _drop_markup(norm: str, lines: list[int], strip_tags: bool) -> tuple[str, list[int]]:
    keep = [True] * len(norm)
    spans = [m.span() for m in COMMENT.finditer(norm)]
    spans += [m.span() for m in MARKDOWN_MARKS.finditer(norm)]
    if strip_tags:
        spans += [
            m.span() for m in TAG.finditer(norm) if m.group(1) in HTML_ELEMENTS or "-" in m.group(1)
        ]
    for start, end in spans:
        for i in range(start, end):
            # keep line breaks that sit inside a comment so later line numbers stay right
            keep[i] = keep[i] and norm[i] == "\n"
    return (
        "".join(c for c, k in zip(norm, keep, strict=True) if k),
        [n for n, k in zip(lines, keep, strict=True) if k],
    )


def scan_text(path: str, text: str) -> list[Finding]:
    """All rule matches in text. Phrases split across lines or by hyphen variants still match."""
    views = [_normalise(text), _normalise(text, strip_tags=False)]
    source_lines = unicodedata.normalize("NFKC", text).split("\n")
    found: list[Finding] = []
    for norm, line_of in views:
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
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def scan_tree(root: Path, allow: dict[str, Any]) -> list[Finding]:
    """Scan every tracked or untracked-unignored file that is text.

    A file with a known text suffix must be UTF-8 or it is a finding. Any other file is read
    too: it is scanned when it is UTF-8 without NUL bytes, and skipped as binary otherwise
    (known binary suffixes are not read), so a new suffix cannot hide a claim.
    """
    findings: list[Finding] = []
    for path in _repo.tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if _excluded(rel, allow["excluded_paths"]) or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        data = path.read_bytes()
        known = _is_text(path)
        if not known and b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if known:
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
