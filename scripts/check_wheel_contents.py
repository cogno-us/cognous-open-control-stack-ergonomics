"""Wheel contents check: what must ship, and what must never ship.

Takes the wheel path (or a directory holding exactly one wheel).
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

REQUIRED = [
    "ocs/__init__.py",
    "ocs/py.typed",
    "ocs/cli.py",
    "ocs/fixtures/customer-service/manifest.json",
    "ocs/fixtures/customer-service/run-record.json",
    "ocs/fixtures/customer-service/replay-bundle.json",
    "ocs/fixtures/customer-service/evidence-pack.json",
    "ocs/fixtures/customer-service/provenance.json",
    "ocs/fixtures/customer-service/acp-run/manifest.json",
    "ocs/fixtures/customer-service/acp-run/authority-context.json",
    "ocs/fixtures/customer-service/acp-run/resolver-state.json",
    "ocs/fixtures/customer-service/acp-run/proposal-crm-read.json",
    "ocs/fixtures/customer-service/acp-run/proposal-notes-search.json",
    "ocs/fixtures/customer-service/acp-run/proposal-email-send.json",
    "ocs/schema_resources/SNAPSHOTS.json",
    "ocs/schema_resources/v1/envelope.schema.json",
    "ocs/schema_resources/v1/diagnostics.schema.json",
]
REQUIRED_PREFIXES = ["ocs/schema_resources/v1/"]
FORBIDDEN_PREFIXES = (
    "tests/",
    "scripts/",
    ".github/",
    ".githooks/",
    "schemas/",
    "ocs/schema_resources/unreleased/",
)
FORBIDDEN_NAMES = {"release-state.json", "Makefile", ".coverage"}


def check(wheel: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        for need in REQUIRED:
            if need not in names:
                errors.append(f"missing from wheel: {need}")
        for prefix in REQUIRED_PREFIXES:
            if not any(n.startswith(prefix) for n in names):
                errors.append(f"nothing under {prefix}")
        for n in names:
            if n.startswith(FORBIDDEN_PREFIXES) or n.rsplit("/", 1)[-1] in FORBIDDEN_NAMES:
                errors.append(f"must not ship: {n}")
            if "__pycache__" in n or n.endswith(".pyc"):
                errors.append(f"bytecode in wheel: {n}")
        if not any(
            n.endswith(".dist-info/licenses/LICENSE") or n.endswith(".dist-info/LICENSE")
            for n in names
        ):
            errors.append("LICENSE missing from dist-info")
        entry = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        if entry is None or "ocs = ocs.cli:main" not in zf.read(entry).decode():
            errors.append("console script `ocs = ocs.cli:main` missing")
        meta = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if (
            meta is None
            or "Requires-Dist: agent-" in zf.read(meta).decode().split("Provides-Extra: stack")[0]
        ):
            errors.append("components must stay an optional extra, not a core dependency")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", type=Path)
    path = ap.parse_args(argv).path
    wheels = sorted(path.glob("*.whl")) if path.is_dir() else [path]
    if len(wheels) != 1:
        print(f"expected exactly one wheel, found {len(wheels)}")
        return 1
    errors = check(wheels[0])
    print("\n".join(errors) if errors else f"wheel contents: ok ({wheels[0].name})")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
