"""`cognous-ocs manifest|replay|evidence ...`: thin wrappers over the component CLIs.

`cognous-ocs manifest ARGS` runs `aam ARGS`, `cognous-ocs replay ARGS` runs `arb ARGS` and `cognous-ocs evidence ARGS`
runs `agep ARGS` (except `cognous-ocs evidence render`, which is `cognous-ocs`'s own HTML view). Everything after
the layer name belongs to the component, verbatim; `cognous-ocs` options (`--format`, `--strict`,
`--no-color`) go before it. In text mode the component's output and exit code are relayed as
they are. With `--format json` the run is wrapped in the standard envelope instead, and the
component's exit code is mapped, not reinterpreted.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cognous_ocs.demo import ComponentProblem, installed_commit
from cognous_ocs.envelope import (
    ERROR,
    FAIL,
    INVALID,
    NOT_EVALUATED,
    OCS_BLOCKING_FINDING,
    OCS_INPUT_INVALID,
    OCS_INTERNAL,
    OCS_NOT_APPLICABLE,
    OCS_VERSION,
    PASS,
    Result,
    strip_controls,
)
from cognous_ocs.lock import COMPONENTS
from cognous_ocs.runner import ComponentRunError, check_pins, run_component

LAYERS = {"manifest": "aam", "replay": "arb", "evidence": "agep"}
FORMATS = ("text", "json")
TIMEOUT_SECONDS = 600.0
OUTPUT_IN_RESULT = 4_000

DOES_NOT_PROVE = [
    "that cognous-ocs interpreted anything: the component's own command ran and its exit code is mapped",
    "external source linkage",
    "producer authenticity",
]

# Component exit code to (outcome, reason code). The contract's codes 0 to 4 are relayed as they
# are; anything else is not a documented verdict and becomes an internal error.
EXIT_MAP = {
    1: (FAIL, OCS_BLOCKING_FINDING),
    2: (INVALID, OCS_INPUT_INVALID),
    3: (NOT_EVALUATED, OCS_NOT_APPLICABLE),
    4: (ERROR, OCS_INTERNAL),
}


# The component subcommands whose exit 0 is a validation verdict (the public validators the
# registry also drives, plus `arb verify`, which checks a signed bundle). Any other invocation
# (help, version, list, summarize, sign, an unknown subcommand) exiting 0 only says the command
# ran, so under `--format json` it is `not_evaluated`, never `pass`. Text mode relays the component's
# exit code as it is (`cognous-ocs manifest --help` still exits 0) and carries no envelope.
VERDICT_SUBCOMMANDS = {
    "aam": frozenset({"validate"}),
    "arb": frozenset({"validate", "verify"}),
    "agep": frozenset({"validate"}),
}
INFO_FLAGS = frozenset({"--help", "-h", "--version", "-V"})


@dataclass(frozen=True)
class Passthrough:
    layer: str
    command: str  # component command
    rest: list[str]
    fmt: str


def detect(argv: list[str]) -> Passthrough | None:
    """Recognise `[cognous-ocs options] LAYER ARGS...` where LAYER is a passthrough layer."""
    i = 0
    fmt = "text"
    while i < len(argv):
        arg = argv[i]
        if arg == "--format" and i + 1 < len(argv) and argv[i + 1] in FORMATS:
            fmt = argv[i + 1]
            i += 2
        elif arg.startswith("--format=") and arg.removeprefix("--format=") in FORMATS:
            fmt = arg.removeprefix("--format=")
            i += 1
        elif arg in ("--strict", "--no-color"):
            i += 1
        else:
            break
    if i >= len(argv) or argv[i] not in LAYERS:
        return None
    layer, rest = argv[i], argv[i + 1 :]
    if layer == "evidence" and (not rest or rest[0].startswith("-") or rest[0] == "render"):
        return None  # `cognous-ocs evidence render ...` and bare `cognous-ocs evidence` stay with cognous-ocs
    return Passthrough(layer, LAYERS[layer], rest, fmt)


def is_verdict_invocation(pt: Passthrough) -> bool:
    """True only for a documented validating subcommand with no help or version flag."""
    if not pt.rest or pt.rest[0].startswith("-") or any(a in INFO_FLAGS for a in pt.rest):
        return False
    return pt.rest[0] in VERDICT_SUBCOMMANDS.get(pt.command, frozenset())


def _label(pt: Passthrough) -> str:
    """The command label for the envelope: layer and subcommand, never the arguments."""
    sub = pt.rest[0] if pt.rest and not pt.rest[0].startswith("-") else ""
    return strip_controls(f"{pt.layer} {sub}".strip())


def _text(raw: str) -> str:
    return "".join(ch if ch in "\n\t" else strip_controls(ch) for ch in raw)


def execute(
    pt: Passthrough,
    *,
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
    timeout: float = TIMEOUT_SECONDS,
) -> tuple[Result, bool]:
    """Run the component. Returns the result and whether text output was already relayed."""
    label = _label(pt)
    subject = {"kind": "component-command", "path": pt.command}
    try:
        directory = check_pins([pt.command], bin_dir, commit_of)
    except ComponentProblem as exc:
        return Result(label, INVALID, [OCS_VERSION], subject, messages=[str(exc)]), False
    try:
        ran = run_component(directory, pt.command, pt.rest, cwd=None, timeout=timeout, limit=None)
    except ComponentRunError as exc:
        return Result(label, ERROR, [OCS_INTERNAL], subject, messages=[str(exc)]), False
    relayed = False
    if pt.fmt == "text":
        sys.stdout.write(_text(ran.stdout))
        sys.stderr.write(_text(ran.stderr))
        if ran.code not in (0, *EXIT_MAP):
            sys.stderr.write(
                f"ocs: {pt.command} exited {ran.code}, which is not a documented verdict; "
                "reporting exit 4\n"
            )
        sys.stdout.flush()
        sys.stderr.flush()
        relayed = True
    comp = COMPONENTS[pt.command]
    source = {
        "component": comp.key.replace("_", "-"),
        "contract": comp.contract,
        "contract_version": _version_of(comp.distribution),
    }
    extensions = {
        "x-component-exit": ran.code,
        "x-component-output": _text(ran.text)[:OUTPUT_IN_RESULT],
    }
    if ran.code == 0 and pt.fmt == "json" and not is_verdict_invocation(pt):
        return (
            Result(
                label,
                NOT_EVALUATED,
                [OCS_NOT_APPLICABLE],
                subject,
                messages=[
                    f"{pt.command} exited 0, but this invocation is not a documented validating "
                    "subcommand: it carries no verdict"
                ],
                extensions=extensions,
            ),
            relayed,
        )
    if ran.code == 0:
        return (
            Result(
                label,
                PASS,
                [],
                subject,
                verdict_source=source,
                does_not_prove=list(DOES_NOT_PROVE),
                messages=[f"{pt.command} exited 0"],
                extensions=extensions,
            ),
            relayed,
        )
    outcome, reason = EXIT_MAP.get(ran.code, (ERROR, OCS_INTERNAL))
    note = (
        f"{pt.command} exited {ran.code}"
        if ran.code in EXIT_MAP
        else f"{pt.command} exited {ran.code}, which is not a documented verdict"
    )
    return (
        Result(
            label,
            outcome,
            [reason],
            subject,
            verdict_source=source,
            messages=[note],
            extensions=extensions,
        ),
        relayed,
    )


def _version_of(distribution: str) -> str:
    import importlib.metadata as md

    try:
        return md.version(distribution)
    except md.PackageNotFoundError:
        return "unknown"
