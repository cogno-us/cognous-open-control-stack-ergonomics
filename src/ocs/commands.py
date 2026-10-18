"""Subcommand registration. Each command module adds itself here."""

from __future__ import annotations

import argparse
import hashlib
from typing import Any

from ocs import cli, demo, registry, render, validate
from ocs.envelope import (
    INVALID,
    OCS_PATHS,
    PASS,
    Result,
)
from ocs.paths import PathRefused, check_input, confine_output, refuse_alias


def evidence_render(args: argparse.Namespace) -> cli.Outcome:
    """Render one evidence pack to one self-contained HTML file."""
    subject: dict[str, Any] = {"kind": "evidence-pack", "path": args.path}
    try:
        source = check_input(args.path)
        out = confine_output(args.out)
        refuse_alias([source], [out])
    except PathRefused as exc:
        return cli.Outcome(
            Result("evidence render", INVALID, [OCS_PATHS], subject, messages=[str(exc)])
        )
    diag_dir = out.parent
    try:
        pack = render.load_pack(source)
    except render.RenderRefused as exc:
        return cli.Outcome(
            Result("evidence render", INVALID, [exc.code], subject, messages=[exc.message]),
            diag_dir,
        )
    subject["declared_version"] = pack.declared_version
    page = render.render_html(pack.data).encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(page)
    digest = hashlib.sha256(page).hexdigest()
    return cli.Outcome(
        Result(
            "evidence render",
            PASS,
            [],
            subject,
            does_not_prove=list(render.DOES_NOT_PROVE),
            messages=[
                f"rendered the pack as given to {out.name} (sha256 {digest[:16]})",
                "the pack was not validated by this command",
            ],
            strict=bool(getattr(args, "strict", False)),
        ),
        diag_dir,
    )


def demo_command(args: argparse.Namespace) -> cli.Outcome:
    return demo.run_demo(
        args.out, clean=bool(args.clean), strict=bool(getattr(args, "strict", False))
    )


def validate_command(args: argparse.Namespace) -> cli.Outcome:
    return validate.run_validate(args.path, args.kind)


def register(sub: argparse._SubParsersAction[Any], common: argparse.ArgumentParser) -> None:
    evidence = sub.add_parser("evidence", parents=[common], help="evidence pack views")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", parser_class=type(evidence))
    r = evidence_sub.add_parser("render", parents=[common], help="one self-contained HTML view")
    r.add_argument("path", help="evidence pack JSON")
    r.add_argument("--out", required=True, help="HTML file to write")
    d = sub.add_parser("demo", parents=[common], help="run a bundled example offline")
    d.add_argument("example", choices=("customer-service",))
    d.add_argument("--out", required=True, help="directory to write (created or replaced)")
    d.add_argument(
        "--clean", action="store_true", help="replace a directory a previous demo created"
    )
    v = sub.add_parser("validate", parents=[common], help="check one artifact")
    v.add_argument("path", help="artifact JSON")
    v.add_argument("--kind", required=True, choices=registry.KINDS, help="what the artifact is")
    cli.HANDLERS["demo"] = demo_command
    cli.HANDLERS["validate"] = validate_command
    cli.HANDLERS["evidence"] = _evidence_dispatch


def _evidence_dispatch(args: argparse.Namespace) -> cli.Outcome:
    if getattr(args, "evidence_command", None) == "render":
        return evidence_render(args)
    raise cli.UsageError("evidence needs a subcommand: render")
