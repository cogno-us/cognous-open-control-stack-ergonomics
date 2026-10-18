"""Command-line entry point. Parsing, dispatch, output and exit codes live here."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from ocs import CONTRACT_VERSION, EXAMPLE_NAME, __version__
from ocs.envelope import (
    ERROR,
    INVALID,
    OCS_INPUT_INVALID,
    OCS_INTERNAL,
    OCS_PATHS,
    Result,
    write_diagnostics,
)
from ocs.fixtures_data import example_version
from ocs.lock import COMPONENTS
from ocs.paths import PathRefused, confine_output


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise UsageError(message or "")


@dataclass
class Outcome:
    """A handler's result plus the directory (already confined) that may hold diagnostics."""

    result: Result
    diagnostics_dir: Path | None = None
    read_only: bool = False  # never write, not even ci-diagnostics.json (doctor)


Handler = Callable[[argparse.Namespace], Outcome]
HANDLERS: dict[str, Handler] = {}


def _common() -> argparse.ArgumentParser:
    """Global options, accepted before or after the subcommand."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--format", choices=("text", "json"), default=argparse.SUPPRESS)
    p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS)
    p.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)
    return p


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="ocs", description="Front door to the Cognous Open Control Stack.")
    parser.add_argument(
        "--version", action="store_true", help="print CLI, contract and bundled versions"
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    _register(sub)
    return parser


def _register(sub: argparse._SubParsersAction[_Parser]) -> None:
    """Subcommands are added by their own modules as they land."""
    from ocs import commands

    commands.register(sub, _common())


def version_text() -> str:
    lines = [f"ocs {__version__}", f"cli-contract {CONTRACT_VERSION}"]
    lines.append(f"bundled-example {EXAMPLE_NAME} {example_version()}")
    for c in COMPONENTS.values():
        lines.append(f"bundled-component {c.command} {c.repository}@{c.sha[:12]}")
    return "\n".join(lines) + "\n"


def _wants_json(argv: Sequence[str]) -> bool:
    args = list(argv)
    return "--format=json" in args or any(
        a == "--format" and i + 1 < len(args) and args[i + 1] == "json" for i, a in enumerate(args)
    )


def _emit(result: Result, fmt: str) -> None:
    sys.stdout.write(result.to_json() if fmt == "json" else result.to_text())
    sys.stdout.flush()


def _diagnostics_dir(declared: Path | None) -> Path | None:
    if declared is not None:
        return declared
    env = os.environ.get("OCS_DIAGNOSTICS_DIR")
    if env:
        try:
            return confine_output(env)
        except PathRefused:
            return None
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    from ocs import passthrough  # lazy: passthrough imports modules that import this one

    pt = passthrough.detect(args_in)
    if pt is not None:
        pt_result, relayed = passthrough.execute(pt)
        return _finish(pt_result, pt.fmt, None, quiet=relayed)
    fmt = "json" if _wants_json(args_in) else "text"
    parser = build_parser()
    try:
        args = parser.parse_args(args_in)
    except UsageError as exc:
        result = Result(
            command="ocs",
            outcome=INVALID,
            reason_codes=[OCS_INPUT_INVALID],
            messages=[f"usage error: {exc}"],
        )
        return _finish(result, fmt, None)
    if args.version:
        sys.stdout.write(version_text())
        return 0
    if args.command is None:
        result = Result(
            command="ocs",
            outcome=INVALID,
            reason_codes=[OCS_INPUT_INVALID],
            messages=["no command given; see ocs --help"],
        )
        return _finish(result, fmt, None)
    handler = HANDLERS[args.command]
    diag: Path | None = None
    read_only = False
    try:
        outcome = handler(args)
        result, diag, read_only = outcome.result, outcome.diagnostics_dir, outcome.read_only
    except UsageError as exc:
        result = Result(
            command=args.command,
            outcome=INVALID,
            reason_codes=[OCS_INPUT_INVALID],
            messages=[f"usage error: {exc}"],
        )
    except PathRefused as exc:
        result = Result(
            command=args.command, outcome=INVALID, reason_codes=[OCS_PATHS], messages=[str(exc)]
        )
    except Exception as exc:  # last-resort guard; content is never echoed
        result = Result(
            command=args.command,
            outcome=ERROR,
            reason_codes=[OCS_INTERNAL],
            messages=[f"internal error ({type(exc).__name__})"],
        )
    return _finish(result, fmt, diag, read_only=read_only)


def _finish(
    result: Result, fmt: str, diag: Path | None, *, read_only: bool = False, quiet: bool = False
) -> int:
    if not quiet:  # quiet: a passthrough already relayed the component's own output
        _emit(result, fmt)
    code = result.exit_code
    if code != 0 and not read_only:
        target = _diagnostics_dir(diag)
        if target is not None:
            with contextlib.suppress(OSError):
                write_diagnostics(result, target)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
