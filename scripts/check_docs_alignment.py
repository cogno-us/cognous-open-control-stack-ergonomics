"""Docs alignment and local link check.

Checks, offline and without importing component code:
- every local Markdown link (and #anchor into a Markdown file) in README.md and docs/ resolves;
- every docs/*.md page is linked from the README contents list;
- the README and cli-contract state the package version and contract version the code reports;
- every subcommand the parser registers appears in the cli-contract command table;
- the cli-contract exit-code table equals the envelope's exit-code mapping.
Documented-but-unbuilt commands are allowed: the contract may run ahead of the code, never behind.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^(```|~~~)")


def _slug(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def anchors(md: Path) -> set[str]:
    out: set[str] = set()
    fenced = False
    for line in md.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            out.add(_slug(line.lstrip("#")))
    return out


def links(md: Path) -> list[str]:
    found: list[str] = []
    fenced = False
    for line in md.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            found += LINK.findall(line)
    return found


def check_links(root: Path, pages: list[Path]) -> list[str]:
    errors: list[str] = []
    for page in pages:
        for target in links(page):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue  # external or mailto: not checked offline
            ref, _, frag = target.partition("#")
            dest = page if not ref else (page.parent / ref).resolve()
            rel = page.relative_to(root).as_posix()
            if not dest.exists():
                errors.append(f"{rel}: broken link {target}")
            elif frag and dest.suffix == ".md" and frag not in anchors(dest):
                errors.append(f"{rel}: missing anchor {target}")
    return errors


def check_contents(root: Path) -> list[str]:
    readme = (root / "README.md").read_text(encoding="utf-8")
    return [
        f"README.md: docs/{p.name} is not linked from the README"
        for p in sorted((root / "docs").glob("*.md"))
        if f"docs/{p.name}" not in readme
    ]


def _code_facts(root: Path) -> tuple[str, str, set[str], dict[int, str]]:
    sys.path.insert(0, str(root / "src"))
    from ocs import CONTRACT_VERSION, __version__
    from ocs.cli import build_parser
    from ocs.envelope import OUTCOMES, exit_code_for

    parser = build_parser()
    commands: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 - argparse has no public listing
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            commands |= set(action.choices)
    codes: dict[int, str] = {}
    for outcome in sorted(OUTCOMES):
        codes.setdefault(exit_code_for(outcome), outcome)
    return __version__, CONTRACT_VERSION, commands, codes


def check_versions_and_commands(root: Path) -> list[str]:
    errors: list[str] = []
    version, contract, commands, codes = _code_facts(root)
    contract_md = (root / "docs" / "cli-contract.md").read_text(encoding="utf-8")
    if f"(draft {contract})" not in contract_md.splitlines()[0]:
        errors.append(f"docs/cli-contract.md: title does not name draft {contract}")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    if f'version = "{version}"' not in pyproject:
        errors.append(f"pyproject.toml: version differs from ocs.__version__ {version}")
    table = contract_md.split("## 2. Commands", 1)[1].split("## 3.", 1)[0]
    for name in sorted(commands):
        if not re.search(rf"`ocs {re.escape(name)}[ `\[]", table):
            errors.append(f"docs/cli-contract.md: registered command {name!r} is not in section 2")
    exit_table = contract_md.split("## 3. Exit codes", 1)[1].split("Rules:", 1)[0]
    documented = {int(m.group(1)) for m in re.finditer(r"^\| (\d) \|", exit_table, re.M)}
    if documented != set(codes):
        errors.append(
            f"docs/cli-contract.md: exit codes {sorted(documented)} != code {sorted(codes)}"
        )
    return errors


def run(root: Path) -> list[str]:
    pages = [root / "README.md", *sorted((root / "docs").glob("*.md"))]
    return check_links(root, pages) + check_contents(root) + check_versions_and_commands(root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    errors = run(ap.parse_args(argv).root)
    print("\n".join(errors) if errors else "docs alignment: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
