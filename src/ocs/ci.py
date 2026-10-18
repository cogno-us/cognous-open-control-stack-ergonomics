"""`ocs ci`: the entry the composite action calls. Validate, gate, write reports and diagnostics.

It decides nothing. Every check is a component verdict relayed through `ocs validate` (or the
read-only `ocs doctor` consistency checks), and the overall outcome is the worst step outcome.

Fail-closed profile: it refuses to run unless exactly one of a strict evidence input
(`--evidence-pack`) or the explicit non-assurance mode (`--non-assurance`) is chosen. In
non-assurance mode no evidence pack is checked, so even when every step passes the outcome is
`review`, never `pass`.

Version binding: with `--expect-version V` the installed `ocs` must report exactly V, else exit 2
(`OCS_VERSION`) before anything else runs.

Output: everything goes under `--out` (inside the workspace or temp directory, no symlinks). An
existing directory must hold only files this command owns; each run replaces them.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ocs import __version__, validate
from ocs.cli import Outcome
from ocs.doctor import FAILED, _confined, _read, run_checks
from ocs.envelope import (
    ERROR,
    FAIL,
    INVALID,
    OCS_INPUT_INVALID,
    OCS_PATHS,
    OCS_VERSION,
    PASS,
    REVIEW,
    Result,
    strip_controls,
)
from ocs.paths import PathRefused, check_input, confine_output
from ocs.scaffold import WORKFLOW_FILE

REPORT_MODES = ("full", "fail-fast")
SUMMARY_JSON = "ci-summary.json"
SUMMARY_MD = "ci-summary.md"
REPORTS_DIR = "reports"
EVIDENCE_DIR = "evidence"
INPUTS_DIR = "inputs"
DIAGNOSTICS = "ci-diagnostics.json"
OWNED = frozenset({SUMMARY_JSON, SUMMARY_MD, DIAGNOSTICS, REPORTS_DIR, EVIDENCE_DIR, INPUTS_DIR})
SAFE_TEXT = re.compile(r"[^\w ./@+:=,;()'-]")

CI_DOES_NOT_PROVE = [
    "that the evidence pack describes what really happened, or that any run record came from the "
    "control plane",
    "that a workflow is authorised to run, or anything about deployment or release status",
]
NON_ASSURANCE_NOTE = (
    "non-assurance mode: no evidence pack was checked, so the outcome is review, never pass"
)


@dataclass
class Step:
    label: str
    kind: str | None  # validate kind, or None for the doctor consistency checks
    path: Path | None
    outcome: str = "not_run"
    exit_code: int | None = None
    reason_codes: list[str] = field(default_factory=list)
    verdict_source: dict[str, str] | None = None
    messages: list[str] = field(default_factory=list)
    report: dict[str, Any] | None = None

    def record(self, shown: str | None) -> dict[str, Any]:
        return {
            "check": self.label,
            "path": shown,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "reason_codes": list(self.reason_codes),
            "verdict_source": self.verdict_source,
        }


def _refuse(code: str, message: str, out: Path | None, subject: dict[str, Any]) -> Outcome:
    result = Result("ci", INVALID, [code], subject, messages=[message])
    return Outcome(result, out)


def _owned_only(out: Path) -> bool:
    return all(child.name in OWNED for child in out.iterdir())


def _clear_owned(out: Path) -> None:
    for name in OWNED - {DIAGNOSTICS}:
        target = out / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()


def _within(path: Path, directory: Path) -> bool:
    resolved, base = path.resolve(), directory.resolve()
    return resolved == base or base in resolved.parents


def _collect(
    manifest: str | None, workflow: str | None, pack: str | None
) -> tuple[list[Step], list[Path], str | None]:
    """Build the step list and the input files. Returns an error text instead on refusal."""
    steps: list[Step] = []
    files: list[Path] = []
    if manifest is not None:
        path = check_input(manifest)
        steps.append(Step("validate manifest", "manifest", path))
        files.append(path)
    if workflow is not None:
        base = Path(workflow)
        if any(c in workflow for c in ("\r", "\n", "\x00")) or not base.is_dir():
            return [], [], "the workflow path is not a readable directory"
        steps.append(Step("setup consistency (doctor)", None, base))
        settings, _why = _read(base / WORKFLOW_FILE)
        if settings is not None:
            for key, kind, label in (
                ("manifest", "manifest", "validate workflow manifest"),
                ("run_record", "run-record", "validate workflow run record"),
            ):
                resolved, problem = _confined(base, settings.get(key))
                if resolved is None or problem or not resolved.is_file():
                    continue  # the doctor step reports the reference problem
                steps.append(Step(label, kind, resolved))
                files.append(resolved)
    if pack is not None:
        path = check_input(pack)
        steps.append(Step("validate evidence pack", "evidence-pack", path))
        files.append(path)
    return steps, files, None


def _run_step(step: Step, workflow: str | None) -> None:
    if step.kind is None:
        checks = run_checks(workflow)
        failed = [c for c in checks if c.status == FAILED]
        step.outcome = INVALID if failed else PASS
        step.exit_code = 2 if failed else 0
        step.reason_codes = [c.code for c in failed]
        step.messages = [f"{c.status} {c.code}: {c.message}" for c in checks]
        return
    if step.path is None:
        raise ValueError("a validate step needs a path")
    outcome = validate.run_validate(str(step.path), step.kind)
    result = outcome.result
    step.outcome = result.outcome
    step.exit_code = result.exit_code
    step.reason_codes = list(result.reason_codes)
    step.verdict_source = result.verdict_source
    step.messages = [strip_controls(m) for m in result.messages]
    step.report = result.to_dict()


def overall(steps: list[Step], *, assurance: bool) -> str:
    ran = [s.outcome for s in steps if s.outcome != "not_run"]
    if ERROR in ran:
        return ERROR
    if INVALID in ran:
        return INVALID
    if FAIL in ran:
        return FAIL
    if any(o != PASS for o in ran):
        return REVIEW
    return PASS if assurance and ran else REVIEW


def _summary_md(result: Result, steps: list[Step], shown: dict[int, str | None]) -> str:
    def clean(text: object) -> str:
        return SAFE_TEXT.sub("?", strip_controls(text))

    lines = [
        "## ocs ci",
        "",
        f"Outcome: **{result.outcome}** (exit {result.exit_code}). "
        f"Reason codes: {clean(', '.join(result.reason_codes) or 'none')}.",
        "",
        "| Check | Path | Outcome | Reason codes |",
        "|---|---|---|---|",
    ]
    for i, s in enumerate(steps):
        lines.append(
            f"| {clean(s.label)} | {clean(shown[i] or '-')} | {s.outcome} | "
            f"{clean(', '.join(s.reason_codes) or '-')} |"
        )
    lines += ["", "Does not prove:"]
    lines += [f"- {clean(d)}" for d in result.does_not_prove]
    return "\n".join(lines) + "\n"


def run_ci(
    out_raw: str,
    *,
    manifest: str | None = None,
    workflow: str | None = None,
    evidence_pack: str | None = None,
    non_assurance: bool = False,
    report_mode: str = "full",
    expect_version: str | None = None,
    copy_inputs: bool = False,
    strict: bool = False,
) -> Outcome:
    subject: dict[str, Any] = {"kind": "ci", "path": out_raw}
    try:
        out = confine_output(out_raw)
    except PathRefused as exc:
        return Outcome(Result("ci", INVALID, [OCS_PATHS], subject, messages=[str(exc)]))
    if out.exists() and not (out.is_dir() and _owned_only(out)):
        return Outcome(
            Result(
                "ci",
                INVALID,
                [OCS_PATHS],
                subject,
                messages=["the output directory exists and holds files ocs ci does not own"],
            )
        )
    if expect_version is not None and expect_version != __version__:
        return _refuse(
            OCS_VERSION,
            f"installed ocs {__version__} is not the version this was released with "
            f"({strip_controls(expect_version)[:40]})",
            out,
            subject,
        )
    if report_mode not in REPORT_MODES:
        return _refuse(
            OCS_INPUT_INVALID, f"report mode must be one of {REPORT_MODES}", out, subject
        )
    # Fail-closed profile: a strict evidence input or an explicit non-assurance mode, not both.
    if evidence_pack is None and not non_assurance:
        return _refuse(
            OCS_INPUT_INVALID,
            "refusing to run: choose a strict evidence input (--evidence-pack) or the explicit "
            "non-assurance mode (--non-assurance)",
            out,
            subject,
        )
    if evidence_pack is not None and non_assurance:
        return _refuse(
            OCS_INPUT_INVALID,
            "choose one: --evidence-pack (strict) or --non-assurance, not both",
            out,
            subject,
        )
    if manifest is None and workflow is None and evidence_pack is None:
        return _refuse(
            OCS_INPUT_INVALID, "nothing to check: give --manifest or --workflow", out, subject
        )
    try:
        steps, files, problem = _collect(manifest, workflow, evidence_pack)
        if problem is not None:
            return _refuse(OCS_PATHS, problem, out, subject)
        for f in files:
            if _within(f, out):
                raise PathRefused("an input lives inside the output directory")
        if workflow is not None and _within(out, Path(workflow)):
            raise PathRefused("the output directory is inside the workflow directory")
    except PathRefused as exc:
        return Outcome(Result("ci", INVALID, [OCS_PATHS], subject, messages=[str(exc)]), out)

    shown: dict[int, str | None] = {}
    for i, s in enumerate(steps):
        shown[i] = str(s.path) if s.path is not None else None
    for step in steps:
        _run_step(step, workflow)
        if report_mode == "fail-fast" and step.outcome != PASS:
            break

    assurance = evidence_pack is not None
    outcome = overall(steps, assurance=assurance)
    # verdict_source: the first non-passing step that has one, else the last step that has one.
    sourced = [s for s in steps if s.verdict_source is not None]
    failing = [s for s in sourced if s.outcome not in (PASS, "not_run")]
    pick: Step | None = failing[0] if failing else (sourced[-1] if sourced else None)
    codes: list[str] = []
    for s in steps:
        codes += [c for c in s.reason_codes if c not in codes]

    out.mkdir(parents=True, exist_ok=True)
    _clear_owned(out)
    report_paths: list[str] = []
    (out / REPORTS_DIR).mkdir()
    for n, s in enumerate(steps, start=1):
        if s.outcome == "not_run":
            continue
        doc = s.report or {"check": s.label, "outcome": s.outcome, "messages": s.messages}
        name = f"{REPORTS_DIR}/{n:02d}-{(s.kind or 'doctor')}.json"
        (out / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "utf-8")
        report_paths.append(name)
    pack_step = next((s for s in steps if s.kind == "evidence-pack"), None)
    if pack_step is not None and pack_step.outcome == PASS and pack_step.path is not None:
        (out / EVIDENCE_DIR).mkdir()
        shutil.copyfile(pack_step.path, out / EVIDENCE_DIR / "evidence-pack.json")
        report_paths.append(f"{EVIDENCE_DIR}/evidence-pack.json")
    if copy_inputs:
        (out / INPUTS_DIR).mkdir()
        for n, s in enumerate(steps, start=1):
            if s.kind in ("manifest", "run-record") and s.path is not None:
                target = f"{INPUTS_DIR}/{n:02d}-{s.kind}.json"
                shutil.copyfile(s.path, out / target)
                report_paths.append(target)
    messages = [f"{s.label}: {s.outcome}" for s in steps]
    if outcome == REVIEW and not assurance:
        messages.append(NON_ASSURANCE_NOTE)
    result = Result(
        "ci",
        outcome,
        codes,
        subject,
        verdict_source=pick.verdict_source if pick else None,
        does_not_prove=[*validate.DOES_NOT_PROVE, *CI_DOES_NOT_PROVE],
        messages=messages,
        extensions={
            "x-profile": "strict-evidence" if assurance else "non-assurance",
            "x-report-mode": report_mode,
            "x-steps": [s.record(shown[i]) for i, s in enumerate(steps)],
        },
        strict=strict,
        report_paths=[SUMMARY_JSON, SUMMARY_MD, *report_paths],
    )
    (out / SUMMARY_JSON).write_text(result.to_json(), encoding="utf-8")
    (out / SUMMARY_MD).write_text(_summary_md(result, steps, shown), encoding="utf-8")
    return Outcome(result, out)
