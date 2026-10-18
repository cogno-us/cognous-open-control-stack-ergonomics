"""Schema snapshot immutability check, and the one place a new snapshot is recorded.

`schemas/SNAPSHOTS.json` records the sha256 of every file in every `schemas/vN/` snapshot.

Check (default):
- every `schemas/vN/` directory is listed, with exactly the recorded files and hashes;
- every listed snapshot exists;
- for every git tag matching `v[0-9]*`, each `schemas/vN/` directory present at that tag is
  byte-identical now (a tagged snapshot never changes; a change needs a new vN directory).
With no tags yet the manifest is the only reference; the tag comparison starts working the
moment a tag exists. `schemas/unreleased/` may exist as an exporter smoke target; it is never
checked, listed or bundled.

`--record vN` appends a new snapshot to the manifest. It refuses to touch one already recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402

MANIFEST = "schemas/SNAPSHOTS.json"
SCHEMA_ID = "ocs-schema-snapshots/1"
SNAPSHOT_DIR = re.compile(r"^v[0-9]+$")
TAG_PATTERN = "v[0-9]*"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_dirs(root: Path) -> list[str]:
    base = root / "schemas"
    return sorted(p.name for p in base.iterdir() if p.is_dir() and SNAPSHOT_DIR.match(p.name))


def hash_dir(directory: Path) -> dict[str, str]:
    return {p.name: sha(p.read_bytes()) for p in sorted(directory.iterdir()) if p.is_file()}


def load_manifest(root: Path) -> dict[str, dict[str, str]]:
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{MANIFEST} unreadable ({type(exc).__name__})") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "snapshots"}
        or data["schema"] != SCHEMA_ID
    ):
        raise ValueError(f"{MANIFEST} members differ from the closed set")
    snaps = data["snapshots"]
    if not isinstance(snaps, dict) or not all(SNAPSHOT_DIR.match(k) for k in snaps):
        raise ValueError(f"{MANIFEST} snapshot names must look like v1")
    return {k: dict(v) for k, v in snaps.items()}


def write_manifest(root: Path, snaps: dict[str, dict[str, str]]) -> None:
    body = {"schema": SCHEMA_ID, "snapshots": snaps}
    (root / MANIFEST).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def git(root: Path, *args: str) -> bytes | None:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],  # noqa: S607
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def check_manifest(root: Path) -> list[str]:
    try:
        recorded = load_manifest(root)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    present = snapshot_dirs(root)
    for name in present:
        if name not in recorded:
            errors.append(f"schemas/{name}/ is not recorded in {MANIFEST}")
    for name, files in sorted(recorded.items()):
        directory = root / "schemas" / name
        if not directory.is_dir():
            errors.append(f"schemas/{name}/ is recorded but missing")
            continue
        actual = hash_dir(directory)
        for fname in sorted(set(files) | set(actual)):
            if fname not in actual:
                errors.append(f"schemas/{name}/{fname}: recorded file is missing")
            elif fname not in files:
                errors.append(f"schemas/{name}/{fname}: file is not recorded")
            elif actual[fname] != files[fname]:
                errors.append(f"schemas/{name}/{fname}: bytes differ from the recorded hash")
    return errors


def check_tags(root: Path) -> list[str]:
    tags_raw = git(root, "tag", "--list", TAG_PATTERN)
    if not tags_raw:
        return []
    errors: list[str] = []
    for tag in tags_raw.decode().split():
        listing = git(root, "ls-tree", "-r", "-z", f"{tag}", "--", "schemas")
        if listing is None:
            errors.append(f"tag {tag}: cannot list schemas/")
            continue
        for entry in filter(None, listing.decode().split("\0")):
            _meta, _, path = entry.partition("\t")
            parts = path.split("/")
            if len(parts) != 3 or not SNAPSHOT_DIR.match(parts[1]):
                continue
            then = git(root, "show", f"{tag}:{path}")
            now = root / path
            if then is None:
                errors.append(f"tag {tag}: cannot read {path}")
            elif not now.is_file():
                errors.append(f"tag {tag}: {path} was deleted; a tagged snapshot never changes")
            elif now.read_bytes() != then:
                errors.append(f"tag {tag}: {path} changed; add a new vN directory instead")
    return errors


def record(root: Path, name: str) -> list[str]:
    if not SNAPSHOT_DIR.match(name) or not (root / "schemas" / name).is_dir():
        return [f"schemas/{name}/ is not a snapshot directory"]
    try:
        recorded = load_manifest(root)
    except ValueError as exc:
        return [str(exc)]
    if name in recorded:
        return [f"{name} is already recorded; a recorded snapshot is never re-recorded"]
    recorded[name] = hash_dir(root / "schemas" / name)
    write_manifest(root, dict(sorted(recorded.items())))
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    ap.add_argument("--record", metavar="vN", default=None)
    args = ap.parse_args(argv)
    errors = (
        record(args.root, args.record)
        if args.record
        else check_manifest(args.root) + check_tags(args.root)
    )
    if args.record and not errors:
        print(f"recorded {args.record} in {MANIFEST}")
        return 0
    print("\n".join(errors) if errors else "schema snapshots: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
