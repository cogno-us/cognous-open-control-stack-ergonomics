"""Subcommand registration. Each command module adds itself here."""

from __future__ import annotations

import argparse
import hashlib
from typing import Any

from cognous_ocs import (
    ci,
    cli,
    demo,
    doctor,
    passthrough,
    registry,
    render,
    scaffold,
    schemas,
    validate,
)
from cognous_ocs.envelope import (
    INVALID,
    OCS_PATHS,
    PASS,
    Result,
)
from cognous_ocs.paths import PathRefused, check_input, confine_output, refuse_alias


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


def schema_command(args: argparse.Namespace) -> cli.Outcome:
    if getattr(args, "schema_command", None) != "export":
        raise cli.UsageError("schema needs a subcommand: export")
    result, diag_dir = schemas.export(args.out)
    result.strict = bool(getattr(args, "strict", False))
    return cli.Outcome(result, diag_dir)


def ci_command(args: argparse.Namespace) -> cli.Outcome:
    return ci.run_ci(
        args.out,
        manifest=args.manifest,
        workflow=args.workflow,
        evidence_pack=args.evidence_pack,
        non_assurance=bool(args.non_assurance),
        report_mode=args.report_mode,
        expect_version=args.expect_version,
        copy_inputs=bool(args.copy_inputs),
        strict=bool(getattr(args, "strict", False)),
    )


def register(sub: argparse._SubParsersAction[Any], common: argparse.ArgumentParser) -> None:
    for layer, command in passthrough.LAYERS.items():
        if layer == "evidence":
            continue
        # Listed for --help only: cli.main routes these to passthrough before parsing.
        sub.add_parser(
            layer, parents=[common], help=f"run the component command ({command}) as it is"
        ).add_argument("component_args", nargs=argparse.REMAINDER)
        cli.HANDLERS[layer] = _unreachable
    evidence = sub.add_parser(
        "evidence",
        parents=[common],
        help="`render` is cognous-ocs's own; other commands run agep as it is",
    )
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
    i = sub.add_parser("init", parents=[common], help="scaffold a starter workflow")
    i.add_argument("what", choices=("workflow",))
    i.add_argument("--out", required=True, help="directory to scaffold (created if missing)")
    dr = sub.add_parser("doctor", parents=[common], help="read-only setup diagnosis")
    dr.add_argument("--workflow", default=None, help="workflow directory made by cognous-ocs init")
    cli.HANDLERS["init"] = lambda a: scaffold.run_init(a.out)
    cli.HANDLERS["doctor"] = lambda a: doctor.run_doctor(a.workflow)
    sc = sub.add_parser(
        "schema", parents=[common], help="schema snapshots bundled with this release"
    )
    sc_sub = sc.add_subparsers(dest="schema_command", parser_class=type(sc))
    se = sc_sub.add_parser("export", parents=[common], help="write the bundled snapshots to DIR")
    se.add_argument(
        "--out", required=True, help="directory to write (never replaces a differing file)"
    )
    ci_p = sub.add_parser(
        "ci", parents=[common], help="validate, gate and write reports (CI entry)"
    )
    ci_p.add_argument(
        "--out", required=True, help="output directory (only cognous-ocs ci files in it)"
    )
    ci_p.add_argument("--manifest", default=None, help="action manifest to validate")
    ci_p.add_argument("--workflow", default=None, help="workflow directory (see cognous-ocs init)")
    ci_p.add_argument("--evidence-pack", default=None, help="strict evidence input to validate")
    ci_p.add_argument(
        "--non-assurance", action="store_true", help="explicit mode: no evidence pack is checked"
    )
    ci_p.add_argument("--report-mode", choices=ci.REPORT_MODES, default="full")
    ci_p.add_argument(
        "--expect-version", default=None, help="installed cognous-ocs must equal this"
    )
    ci_p.add_argument(
        "--copy-inputs", action="store_true", help="also copy raw inputs to the output"
    )
    da = sub.add_parser("dashboard", parents=[common], help="one static HTML reading of reports")
    da.add_argument("inputs", nargs="*", help="reports, packs, run records or demo directories")
    da.add_argument("--out", required=True, help="HTML file to write")
    cli.HANDLERS["dashboard"] = _dashboard_dispatch
    cli.HANDLERS["ci"] = ci_command
    cli.HANDLERS["schema"] = schema_command
    cli.HANDLERS["demo"] = demo_command
    cli.HANDLERS["validate"] = validate_command
    cli.HANDLERS["evidence"] = _evidence_dispatch


def _unreachable(args: argparse.Namespace) -> cli.Outcome:
    raise cli.UsageError("passthrough commands take the component's arguments after the layer name")


def _evidence_dispatch(args: argparse.Namespace) -> cli.Outcome:
    if getattr(args, "evidence_command", None) == "render":
        return evidence_render(args)
    raise cli.UsageError("evidence needs a subcommand: render, or an agep command")


def _dashboard_dispatch(args: argparse.Namespace) -> cli.Outcome:
    from cognous_ocs import dashboard  # own module; imported here to keep this table's edit small

    outcome: cli.Outcome = dashboard.run_dashboard(
        args.out, args.inputs, strict=bool(getattr(args, "strict", False))
    )
    return outcome
