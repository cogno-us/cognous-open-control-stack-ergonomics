"""`ocs schema export --out DIR`: the schema snapshots bundled with this release.

The snapshots describe what `ocs` itself emits (the result envelope and the diagnostics file),
nothing the components own. They live in `schemas/vN/` in the repository, ship in the wheel as
`ocs/schema_resources/`, and are listed with their sha256 in `SNAPSHOTS.json`. Export verifies
every bundled file against that manifest first, so a damaged copy is never written out, and it
never replaces a differing file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from ocs.envelope import (
    ERROR,
    INVALID,
    OCS_INTERNAL,
    OCS_PATHS,
    PASS,
    Result,
)
from ocs.paths import PathRefused, confine_output

MANIFEST_NAME = "SNAPSHOTS.json"
MANIFEST_SCHEMA = "ocs-schema-snapshots/1"

DOES_NOT_PROVE = [
    "anything about component artifact schemas: the components own those",
    "that an artifact or a result is valid: these are shapes, not verdicts",
    "that the schemas are final: all contracts are still draft",
]


class SnapshotError(Exception):
    """The bundled snapshots are missing or do not match their manifest."""


def _resource_root() -> Any:
    bundled = files("ocs").joinpath("schema_resources")
    if bundled.is_dir():
        return bundled
    # Running from a source checkout: the snapshots live next to src/.
    source = Path(__file__).resolve().parents[2] / "schemas"
    if source.is_dir():
        return source
    raise SnapshotError("no bundled schema snapshots found")


@dataclass(frozen=True)
class Snapshot:
    version: str
    files: dict[str, bytes]


def load_bundled() -> tuple[bytes, list[Snapshot]]:
    """The manifest bytes and every snapshot, each file checked against the manifest."""
    root = _resource_root()
    manifest_bytes: bytes = root.joinpath(MANIFEST_NAME).read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
        recorded: dict[str, dict[str, str]] = manifest["snapshots"]
        if manifest["schema"] != MANIFEST_SCHEMA:
            raise SnapshotError("snapshot manifest has an unknown schema")
    except (ValueError, KeyError, TypeError) as exc:
        raise SnapshotError("snapshot manifest is unreadable") from exc
    snapshots: list[Snapshot] = []
    for version in sorted(recorded):
        loaded: dict[str, bytes] = {}
        for name, digest in sorted(recorded[version].items()):
            data: bytes = root.joinpath(version, name).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise SnapshotError(f"{version}/{name} does not match its recorded hash")
            loaded[name] = data
        snapshots.append(Snapshot(version, loaded))
    return manifest_bytes, snapshots


def export(out_arg: str) -> tuple[Result, Path | None]:
    """Write every bundled snapshot under DIR. All-or-nothing: a differing file stops it."""
    subject: dict[str, Any] = {"kind": "schema-snapshots", "path": out_arg}
    try:
        out = confine_output(out_arg)
    except PathRefused as exc:
        return Result("schema export", INVALID, [OCS_PATHS], subject, messages=[str(exc)]), None
    try:
        manifest_bytes, snapshots = load_bundled()
    except (SnapshotError, OSError) as exc:
        return (
            Result(
                "schema export",
                ERROR,
                [OCS_INTERNAL],
                subject,
                messages=[f"bundled snapshots unusable: {exc}"],
            ),
            out,
        )
    planned: dict[Path, bytes] = {out / MANIFEST_NAME: manifest_bytes}
    for snap in snapshots:
        for name, data in snap.files.items():
            planned[out / snap.version / name] = data
    try:
        for target in planned:
            confine_output(str(target))
    except PathRefused as exc:
        return Result("schema export", INVALID, [OCS_PATHS], subject, messages=[str(exc)]), out
    differing = [
        t
        for t, data in planned.items()
        if t.exists() and (not t.is_file() or t.read_bytes() != data)
    ]
    if differing:
        names = ", ".join(sorted(str(t.relative_to(out)) for t in differing))
        return (
            Result(
                "schema export",
                INVALID,
                [OCS_PATHS],
                subject,
                messages=[f"refusing to replace a differing file: {names}"],
            ),
            out,
        )
    written = 0
    for target, data in planned.items():
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written += 1
    listing = [
        {"path": str(t.relative_to(out)), "sha256": hashlib.sha256(d).hexdigest()}
        for t, d in sorted(planned.items())
    ]
    return (
        Result(
            "schema export",
            PASS,
            [],
            subject,
            does_not_prove=list(DOES_NOT_PROVE),
            messages=[
                f"{len(planned)} files for {', '.join(s.version for s in snapshots)}: "
                f"{written} written, {len(planned) - written} already identical"
            ],
            extensions={"x-files": listing},
        ),
        out,
    )
