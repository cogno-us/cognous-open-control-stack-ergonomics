"""Examples parity: bundled example fixtures are byte-equal to their recorded hashes.

Reads src/ocs/fixtures/<example>/ straight from disk (not through the package), so it also
catches a stray, missing or edited file before a wheel is built. With --source-dir it also
requires the files recorded as byte-equal to the hub example to match that checkout; the
default run is offline and needs no hub checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402

EXAMPLE = "customer-service"
EXPECTED = {"manifest.json", "run-record.json", "replay-bundle.json", "evidence-pack.json"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(root: Path, source_dir: Path | None = None) -> list[str]:
    base = root / "src" / "ocs" / "fixtures" / EXAMPLE
    errors: list[str] = []
    try:
        prov = json.loads((base / "provenance.json").read_text(encoding="utf-8"))
        bundled: dict[str, str] = prov["bundled"]
        equal: list[str] = prov["byte_equal_to_source"]
        sources: dict[str, dict[str, str]] = prov["sources"]
    except (OSError, ValueError, KeyError) as exc:
        return [f"provenance.json unreadable or incomplete ({type(exc).__name__})"]
    if set(bundled) != EXPECTED:
        errors.append(f"provenance lists {sorted(bundled)}, expected {sorted(EXPECTED)}")
    on_disk = {p.name for p in base.iterdir() if p.is_file()}
    if on_disk != EXPECTED | {"provenance.json"}:
        errors.append(f"fixture directory holds {sorted(on_disk)}")
    for name, digest in sorted(bundled.items()):
        if not (base / name).is_file():
            errors.append(f"{name}: missing")
        elif sha(base / name) != digest:
            errors.append(f"{name}: bytes differ from the recorded hash")
    for name in equal:
        if bundled.get(name) != sources.get(name, {}).get("sha256"):
            errors.append(f"{name}: recorded as byte-equal to source but the hashes differ")
        if source_dir is not None:
            theirs = source_dir / name
            if not theirs.is_file() or sha(theirs) != bundled.get(name):
                errors.append(f"{name}: differs from the source checkout")
    return errors


SAMPLE = Path("examples") / "sample-repo"


def check_sample(root: Path) -> list[str]:
    """The sample repository is the scaffold output plus the bundled evidence pack, byte for byte."""
    sys.path.insert(0, str(root / "src"))
    from ocs import scaffold

    errors: list[str] = []
    base = root / SAMPLE
    for name, content in sorted(scaffold.managed_bytes().items()):
        target = base / "workflow" / name
        if not target.is_file() or target.read_bytes() != content:
            errors.append(f"{SAMPLE}/workflow/{name}: differs from `ocs init workflow` output")
    extra = (
        {p.name for p in (base / "workflow").iterdir()} - set(scaffold.managed_bytes())
        if (base / "workflow").is_dir()
        else set()
    )
    errors += [f"{SAMPLE}/workflow/{name}: not part of the scaffold" for name in sorted(extra)]
    pack = root / "src" / "ocs" / "fixtures" / EXAMPLE / "evidence-pack.json"
    mine = base / "evidence" / "evidence-pack.json"
    if not mine.is_file() or mine.read_bytes() != pack.read_bytes():
        errors.append(
            f"{SAMPLE}/evidence/evidence-pack.json: differs from the bundled evidence pack"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    ap.add_argument("--source-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    errors = check(args.root, args.source_dir) + check_sample(args.root)
    print("\n".join(errors) if errors else "examples parity: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
