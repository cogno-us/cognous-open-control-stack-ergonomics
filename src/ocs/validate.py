"""`ocs validate PATH --kind KIND`: one artifact, checked by the component's own public command.

The `--kind` argument and the version parsed from the file select a registry entry
(ocs.registry). An unregistered pair fails closed, an archival-only pair is never a pass, and a
registered pair is checked by running the component's validator in a subprocess. `ocs` decides
nothing: a component exit 0 is relayed as a pass that states what it does not prove, anything
else is relayed as invalid.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ocs import registry
from ocs.cli import Outcome
from ocs.demo import ComponentProblem, installed_commit
from ocs.envelope import (
    ERROR,
    INVALID,
    OCS_ARCHIVAL_ONLY,
    OCS_INPUT_INVALID,
    OCS_INTERNAL,
    OCS_KIND_UNREGISTERED,
    OCS_PATHS,
    OCS_VERSION,
    PASS,
    Result,
)
from ocs.lock import COMPONENTS
from ocs.paths import PathRefused, check_input
from ocs.runner import ComponentRunError, check_pins, run_component

MAX_BYTES = 64 * 1024 * 1024
OUTPUT_IN_RESULT = 4_000

DOES_NOT_PROVE = [
    "external source linkage: nothing here checks that the artifact matches what really happened",
    "producer authenticity: nothing here checks who produced the artifact",
    "that the artifact is complete, or that its content is true",
    "anything beyond the pinned component validator's own checks",
]


class _Duplicate(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _Duplicate(f"duplicate member {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def load_document(path: Path) -> dict[str, Any]:
    """Parse the artifact strictly: UTF-8, one object, no duplicate members, no NaN/Infinity."""
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("file is larger than 64 MiB")
    text = path.read_bytes().decode("utf-8")
    doc = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    if not isinstance(doc, dict):
        raise ValueError("top level is not a JSON object")
    return doc


def _norm(value: str) -> str:
    return value.replace("_", "-").lower()


def run_validate(
    path_raw: str,
    kind: str,
    *,
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
    timeout: float = 60.0,
) -> Outcome:
    command = "validate"
    subject: dict[str, Any] = {"kind": kind, "path": path_raw}
    if kind not in registry.KINDS:
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_INPUT_INVALID],
                subject,
                messages=[f"unknown kind {kind!r}; kinds are: {', '.join(registry.KINDS)}"],
            )
        )
    try:
        source = check_input(path_raw)
    except PathRefused as exc:
        return Outcome(Result(command, INVALID, [OCS_PATHS], subject, messages=[str(exc)]))
    try:
        doc = load_document(source)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # json.JSONDecodeError is a ValueError. The message names the problem, not the content.
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_INPUT_INVALID],
                subject,
                messages=[f"the file is not one strict JSON object ({type(exc).__name__})"],
            )
        )
    version = registry.declared_version(kind, doc)
    subject["declared_version"] = version
    inside = doc.get("artifact_kind")
    if inside is not None and (not isinstance(inside, str) or _norm(inside) != kind):
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_INPUT_INVALID],
                subject,
                messages=[
                    f"the file declares an artifact_kind that is not {kind!r}; "
                    "--kind decides the check and a disagreeing declaration is refused"
                ],
            )
        )
    entry = registry.lookup(kind, version)
    if entry is None:
        shown = (
            "has no usable version member, so it matches no registered pair"
            if version is None
            else f"version {version} is not a registered kind/version pair"
        )
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_KIND_UNREGISTERED],
                subject,
                messages=[f"{kind} {shown}"],
            )
        )
    if entry.mode == registry.ARCHIVAL_ONLY:
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_ARCHIVAL_ONLY],
                subject,
                messages=[
                    f"{kind} {version} is registered archival-only, never a pass: {entry.note}"
                ],
            )
        )
    comp = COMPONENTS[entry.command]
    try:
        directory = check_pins([entry.command], bin_dir, commit_of)
    except ComponentProblem as exc:
        return Outcome(Result(command, INVALID, [OCS_VERSION], subject, messages=[str(exc)]))
    try:
        with tempfile.TemporaryDirectory(prefix="ocs-validate-") as scratch:
            ran = run_component(
                directory,
                entry.command,
                [*entry.args, str(source.resolve())],
                cwd=Path(scratch),
                timeout=timeout,
                home=Path(scratch),
            )
    except ComponentRunError as exc:
        return Outcome(Result(command, ERROR, [OCS_INTERNAL], subject, messages=[str(exc)]))
    verdict_source = {
        "component": comp.key.replace("_", "-"),
        "contract": comp.contract,
        "contract_version": _version_of(comp.distribution),
    }
    extensions = {
        "x-check": {"mode": entry.mode, "command": f"{entry.command} {' '.join(entry.args)}"},
        "x-component-exit": ran.code,
        "x-component-output": ran.text[:OUTPUT_IN_RESULT],
    }
    if ran.crashed:
        return Outcome(
            Result(
                command,
                ERROR,
                [OCS_INTERNAL],
                subject,
                verdict_source=verdict_source,
                messages=[f"{entry.command} crashed instead of returning a verdict"],
                extensions=extensions,
            )
        )
    if ran.code == 0:
        return Outcome(
            Result(
                command,
                PASS,
                [],
                subject,
                verdict_source=verdict_source,
                does_not_prove=list(DOES_NOT_PROVE),
                messages=[
                    f"{kind} {version}: artifact-internal validation passed "
                    f"({entry.command} {' '.join(entry.args)} exited 0)"
                ],
                extensions=extensions,
            )
        )
    # Component exit 1 (found errors) and 2 (could not load) both mean the artifact was not
    # accepted: exit 2 for `ocs`. Any other code is not a verdict the component documents.
    if ran.code in (1, 2):
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_INPUT_INVALID],
                subject,
                verdict_source=verdict_source,
                messages=[
                    f"{entry.command} {' '.join(entry.args)} rejected the {kind} "
                    f"(exit {ran.code}); its output is in extensions['x-component-output']"
                ],
                extensions=extensions,
            )
        )
    return Outcome(
        Result(
            command,
            ERROR,
            [OCS_INTERNAL],
            subject,
            verdict_source=verdict_source,
            messages=[f"{entry.command} exited {ran.code}, which is not a documented verdict"],
            extensions=extensions,
        )
    )


def _version_of(distribution: str) -> str:
    import importlib.metadata as md

    try:
        return md.version(distribution)
    except md.PackageNotFoundError:
        return "unknown"
