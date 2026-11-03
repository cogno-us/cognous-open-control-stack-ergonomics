"""`cognous-ocs ci`: the entry the composite action calls. Validate, gate, write reports and diagnostics.

It decides nothing. Every check is a component verdict relayed through `cognous-ocs validate` (or the
read-only `cognous-ocs doctor` consistency checks), and the overall outcome is the worst step outcome.

Fail-closed profile: it refuses to run unless exactly one of a strict evidence input
(`--evidence-pack`) or the explicit non-assurance mode (`--non-assurance`) is chosen. In
non-assurance mode no evidence pack is checked, so even when every step passes the outcome is
`review`, never `pass`.

Version binding: with `--expect-version V` the installed `cognous-ocs` must report exactly V, else exit 2
(`OCS_VERSION`) before anything else runs.

Output: everything goes under `--out` (inside the workspace or temp directory, no symlinks). An
existing directory must hold only files this command owns; each run replaces them.

Run artifacts (`RunArtifacts`): each run reads every input file exactly once into a run-private
snapshot directory, takes its sha256 from the snapshot, validates the snapshot and publishes from the
snapshot, so the bytes that were validated are the bytes that are published. A strict evidence run
(`--evidence-pack`) refuses a symlinked input. Output is built in a run-owned staging directory next to
`--out` and replaces the owned output only when the run completes. Owned names already in `--out` are
cleared as soon as the run starts and again on any failure, so a refused or failed run never leaves
earlier success evidence, a pre-populated file or a stale report behind. `ci-summary.json` records a run
nonce and the input digests under `extensions["x-run"]` (the closed diagnostics schema is unchanged).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cognous_ocs import __version__, validate
from cognous_ocs.cli import Outcome
from cognous_ocs.doctor import FAILED, _confined, _plain_file, _read, run_checks
from cognous_ocs.envelope import (
    ERROR,
    FAIL,
    INVALID,
    OCS_INPUT_INVALID,
    OCS_INTERNAL,
    OCS_PATHS,
    OCS_VERSION,
    PASS,
    REVIEW,
    Result,
    strip_controls,
)
from cognous_ocs.paths import PathRefused, check_input, confine_output
from cognous_ocs.scaffold import WORKFLOW_FILE

REPORT_MODES = ("full", "fail-fast")
SUMMARY_JSON = "ci-summary.json"
SUMMARY_MD = "ci-summary.md"
REPORTS_DIR = "reports"
EVIDENCE_DIR = "evidence"
INPUTS_DIR = "inputs"
MISSING_KIND = "missing-reference"
DIAGNOSTICS = "ci-diagnostics.json"
OWNED = frozenset({SUMMARY_JSON, SUMMARY_MD, DIAGNOSTICS, REPORTS_DIR, EVIDENCE_DIR, INPUTS_DIR})
SAFE_TEXT = re.compile(r"[^\w ./@+:=,;()'-]")
NONCE_ENV = "OCS_RUN_NONCE"
NONCE_RE = re.compile(r"^[0-9a-f]{16,64}$")
RUN_MARKER = "x-run"

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
    snapshot: Snapshot | None = None

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


def _clear_owned(out: Path, keep: frozenset[str] = frozenset()) -> None:
    """Remove the owned names from `out`. `keep` names are an input's ancestors and stay."""
    for name in OWNED - keep:  # a link at an owned name is dropped, never followed
        target = out / name
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()


def copy_regular(source: Path, target: Path, *, follow_source: bool = True) -> None:
    """Copy a file to a new exclusive destination; a link at the destination is never followed.

    With follow_source false the source must be a regular file and not a link either."""
    flags = os.O_RDONLY | (0 if follow_source else os.O_NOFOLLOW)
    src_fd = os.open(source, flags)
    try:
        if not stat.S_ISREG(os.fstat(src_fd).st_mode):
            raise OSError("the source is not a regular file")
        dst_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(src_fd, "rb", closefd=False) as src, os.fdopen(dst_fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
    finally:
        os.close(src_fd)


def _within(path: Path, directory: Path) -> bool:
    resolved, base = path.resolve(), directory.resolve()
    return resolved == base or base in resolved.parents


class SnapshotChanged(Exception):
    """A snapshot no longer holds the bytes whose digest was recorded."""


def _sha256_of(path: Path) -> tuple[str, int]:
    """Digest of a file opened without following a final link."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "rb", closefd=False) as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
        return digest.hexdigest(), size
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Snapshot:
    """One input read exactly once into the run-private directory. The digest comes from this file."""

    path: Path
    sha256: str
    size: int

    def verify(self) -> None:
        try:
            now = _sha256_of(self.path)
        except OSError as exc:
            raise SnapshotChanged("the run snapshot can no longer be read") from exc
        if now != (self.sha256, self.size):
            raise SnapshotChanged("the run snapshot changed after its digest was taken")


def _open_input(source: Path, *, no_links: bool) -> int:
    """Open an input once. With no_links no component of the given path may be a symbolic link.

    Components are opened one by one with O_NOFOLLOW from the working directory, so a parent that
    is swapped for a link after a check is refused at the open itself. An absolute path outside the
    working directory is anchored at its real parent directory (system directories such as a
    linked temp root are not the user's inputs) and only its final component is checked."""
    if not no_links:
        return os.open(source, os.O_RDONLY)
    cwd = os.getcwd()
    if not source.is_absolute():
        anchor, parts = ".", source.parts
    else:
        try:
            anchor, parts = cwd, source.relative_to(cwd).parts
        except ValueError:
            anchor, parts = os.path.realpath(source.parent), (source.name,)
    dir_fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


class RunArtifacts:
    """Everything one `ci` run reads and writes, owned by that run alone.

    - a private snapshot directory (mode 0700) holding each input exactly once, with its digest;
    - a staging directory next to the output directory that is published only on a completed run;
    - a run nonce that `ci-summary.json` records under extensions["x-run"].
    """

    def __init__(self, out: Path, protect: frozenset[str] = frozenset()) -> None:
        self.out = out
        self.protect = protect  # owned top-level names that contain an input: never cleared
        env = os.environ.get(NONCE_ENV, "")
        self.nonce = env if NONCE_RE.match(env) else secrets.token_hex(16)
        self._snapdir: Path | None = None
        self._stage: Path | None = None
        self.inputs: list[dict[str, Any]] = []
        self.published: list[dict[str, Any]] = []

    # start and end

    def reset(self) -> None:
        """Drop earlier owned output before anything else runs (the directory was checked)."""
        if self.out.is_dir() and not self.out.is_symlink():
            _clear_owned(self.out, self.protect)

    def close(self) -> None:
        for held in (self._snapdir, self._stage):
            if held is not None:
                shutil.rmtree(held, ignore_errors=True)
        self._snapdir = self._stage = None

    def abort(self) -> None:
        """A run that did not complete: nothing it or an earlier run produced stays published."""
        self.close()
        with contextlib.suppress(OSError):
            self.reset()

    # inputs

    def snapshot(self, source: Path, name: str, *, no_links: bool) -> Snapshot:
        if self._snapdir is None:
            self._snapdir = Path(tempfile.mkdtemp(prefix="ocs-run-"))
        try:
            fd = _open_input(source, no_links=no_links)
        except OSError as exc:
            reason = "is a symbolic link or goes through one" if no_links else "cannot be read"
            raise PathRefused(f"an input {reason}") from exc
        target = self._snapdir / name
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PathRefused("an input is not a regular file")
            limit = validate.MAX_BYTES
            dst_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, "rb", closefd=False) as src, os.fdopen(dst_fd, "wb") as dst:
                data = src.read(limit + 1)
                if len(data) > limit:
                    raise PathRefused("an input is larger than 64 MiB")
                dst.write(data)
        except OSError as exc:
            raise PathRefused("an input cannot be read") from exc
        finally:
            os.close(fd)
        digest, size = _sha256_of(target)  # the digest of the snapshot, not of the original path
        return Snapshot(target, digest, size)

    # outputs

    def stage(self) -> Path:
        if self._stage is None:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self._stage = Path(
                tempfile.mkdtemp(prefix=f".{self.out.name}.stage-", dir=self.out.parent)
            )
        return self._stage

    def stage_snapshot(self, snap: Snapshot, relative: str) -> None:
        """Copy a snapshot into staging, re-hashing as it goes: a changed snapshot is not published."""
        target = self.stage() / relative
        digest = hashlib.sha256()
        src_fd = os.open(snap.path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            dst_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(src_fd, "rb", closefd=False) as src, os.fdopen(dst_fd, "wb") as dst:
                for block in iter(lambda: src.read(1 << 20), b""):
                    digest.update(block)
                    dst.write(block)
        finally:
            os.close(src_fd)
        if digest.hexdigest() != snap.sha256:
            raise SnapshotChanged("the run snapshot changed before it was published")
        self.published.append({"path": relative, "sha256": snap.sha256, "bytes": snap.size})

    def marker(self) -> dict[str, Any]:
        return {"nonce": self.nonce, "inputs": list(self.inputs), "published": list(self.published)}

    def publish(self) -> None:
        """Replace the owned output with the staging directory. Called only after a completed run."""
        stage = self.stage()
        os.chmod(stage, 0o755)  # noqa: S103 - a normal directory mode, mkdtemp gives 0700
        self.reset()
        if self.out.exists() and not (self.out.is_dir() and _owned_only(self.out)):
            raise PathRefused("the output directory holds files cognous-ocs ci does not own")
        try:
            if self.out.is_dir():
                self.out.rmdir()  # empty after reset, so this does not lose anything
            os.rename(stage, self.out)  # one atomic step
        except OSError:
            self._move_children(stage)
        self._stage = None

    def _move_children(self, stage: Path) -> None:
        """Fallback when the directory itself cannot be replaced (a mount point): move names in,
        the summary last, so an interrupted move never leaves a summary without its evidence."""
        self.out.mkdir(parents=True, exist_ok=True)
        dir_fd = os.open(self.out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for child in sorted(stage.iterdir(), key=lambda c: c.name == SUMMARY_JSON):
                os.rename(child, child.name, dst_dir_fd=dir_fd)
        finally:
            os.close(dir_fd)


@contextlib.contextmanager
def _terminate_as_exception() -> Any:
    """Turn SIGTERM into an exception for the length of the run so the cleanup paths execute."""

    def handler(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _protected_names(out: Path, raw_inputs: list[str | None]) -> frozenset[str]:
    """Top-level owned names under `out` that contain a named input. Clearing them would delete it."""
    keep: set[str] = set()
    for raw in raw_inputs:
        if not raw:
            continue
        try:
            resolved, base = Path(raw).resolve(), out.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if base in resolved.parents and resolved.relative_to(base).parts[0] in OWNED:
            keep.add(resolved.relative_to(base).parts[0])
    return frozenset(keep)


class _QuietParser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        raise argparse.ArgumentError(None, message)


def discard_stale_outputs(argv: list[str]) -> Path | None:
    """For `cli.main` when a `ci` command line fails before or outside `run_ci` (usage error, internal
    error): clear owned output from the `--out` it names, if one can be named and is safe to clear.
    Returns that directory so the caller may write current-run diagnostics into it."""
    parser = _QuietParser(add_help=False)
    for name in ("--out", "--manifest", "--workflow", "--evidence-pack"):
        parser.add_argument(name)
    parser.add_argument("--format")
    try:
        tokens = list(argv)
        # drop the leading global options and the command word
        while tokens and tokens[0] != "ci":
            if tokens[0] == "--format" and len(tokens) > 1:
                tokens = tokens[2:]
            elif tokens[0].startswith("-"):
                tokens = tokens[1:]
            else:
                return None
        known, _rest = parser.parse_known_args(tokens[1:])
        if not known.out:
            return None
        out = confine_output(known.out)
        if out.exists() and not (out.is_dir() and _owned_only(out)):
            return None
        if out.is_dir():
            _clear_owned(
                out, _protected_names(out, [known.manifest, known.workflow, known.evidence_pack])
            )
        return out
    except (PathRefused, OSError, argparse.ArgumentError):
        return None


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
                if resolved is None or problem or not _plain_file(resolved):
                    # a listed artifact that cannot be validated fails the run on its own step
                    steps.append(Step(f"{label} (reference)", MISSING_KIND, None))
                    continue
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
    if step.kind == MISSING_KIND:
        step.outcome = INVALID
        step.exit_code = 2
        step.reason_codes = [OCS_PATHS]
        step.messages = [
            "a workflow artifact named in workflow.json is missing, not a plain file or not confined"
        ]
        return
    if step.path is None:
        raise ValueError("a validate step needs a path")
    # The component and the loader see the run's snapshot, never the original path again.
    checked = step.snapshot.path if step.snapshot is not None else step.path
    outcome = validate.run_validate(str(checked), step.kind)
    result = outcome.result
    step.outcome = result.outcome
    step.exit_code = result.exit_code
    step.reason_codes = list(result.reason_codes)
    step.verdict_source = result.verdict_source
    step.messages = [strip_controls(m) for m in result.messages]
    step.report = result.to_dict()
    if step.snapshot is not None:
        shown = str(step.path)
        for seen in (str(checked.resolve()), str(checked)):
            step.messages = [_relabel(m, seen, shown) for m in step.messages]
            step.report = _relabel(step.report, seen, shown)
        step.report.setdefault("subject", {})["path"] = shown
        try:
            step.snapshot.verify()  # the validated bytes are still the recorded bytes
        except SnapshotChanged as exc:
            step.outcome = ERROR
            step.exit_code = 4
            step.reason_codes = [OCS_INTERNAL]
            step.messages = [str(exc)]
            step.report = {"check": step.label, "outcome": ERROR, "messages": step.messages}


def _relabel(value: Any, old: str, new: str) -> Any:
    """Show the original input path where the snapshot path would otherwise appear."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_relabel(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _relabel(v, old, new) for k, v in value.items()}
    return value


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
        "## cognous-ocs ci",
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
        foreign = out.exists() and not (out.is_dir() and _owned_only(out))
    except PathRefused as exc:
        return Outcome(Result("ci", INVALID, [OCS_PATHS], subject, messages=[str(exc)]))
    except OSError:
        return Outcome(
            Result("ci", INVALID, [OCS_PATHS], subject, messages=["the output path cannot be read"])
        )
    if foreign:
        return Outcome(
            Result(
                "ci",
                INVALID,
                [OCS_PATHS],
                subject,
                messages=[
                    "the output directory exists and holds files cognous-ocs ci does not own"
                ],
            )
        )
    run = RunArtifacts(out, _protected_names(out, [manifest, workflow, evidence_pack]))
    try:
        with _terminate_as_exception():
            run.reset()  # earlier owned output is gone before anything else can fail
            outcome = _run(
                run,
                out,
                subject,
                manifest=manifest,
                workflow=workflow,
                evidence_pack=evidence_pack,
                non_assurance=non_assurance,
                report_mode=report_mode,
                expect_version=expect_version,
                copy_inputs=copy_inputs,
                strict=strict,
            )
        return outcome
    except Exception as exc:  # last-resort guard; content is never echoed
        run.abort()
        result = Result(
            "ci",
            ERROR,
            [OCS_INTERNAL],
            subject,
            messages=[f"internal error ({type(exc).__name__})"],
        )
        return Outcome(result, out)
    except BaseException:  # interruption: leave nothing published, then let it propagate
        run.abort()
        raise
    finally:
        run.close()


def _run(
    run: RunArtifacts,
    out: Path,
    subject: dict[str, Any],
    *,
    manifest: str | None,
    workflow: str | None,
    evidence_pack: str | None,
    non_assurance: bool,
    report_mode: str,
    expect_version: str | None,
    copy_inputs: bool,
    strict: bool,
) -> Outcome:
    if expect_version is not None and expect_version != __version__:
        return _refuse(
            OCS_VERSION,
            f"installed cognous-ocs {__version__} is not the version this was released with "
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
    no_links = evidence_pack is not None  # the strict evidence path refuses symlinked inputs
    try:
        steps, files, problem = _collect(manifest, workflow, evidence_pack)
        if problem is not None:
            return _refuse(OCS_PATHS, problem, out, subject)
        for f in files:
            if _within(f, out):
                raise PathRefused("an input lives inside the output directory")
        if workflow is not None and _within(out, Path(workflow)):
            raise PathRefused("the output directory is inside the workflow directory")
        if copy_inputs and any(
            s.path is not None and s.path.is_symlink()
            for s in steps
            if s.kind in ("manifest", "run-record")
        ):
            raise PathRefused("an input to be copied is a symbolic link")
        # Each input is read exactly once, here; everything below works on the snapshots.
        for n, s in enumerate(steps, start=1):
            if s.kind is not None and s.kind != MISSING_KIND and s.path is not None:
                s.snapshot = run.snapshot(s.path, f"{n:02d}-{s.kind}.json", no_links=no_links)
                run.inputs.append(
                    {
                        "check": s.label,
                        "kind": s.kind,
                        "sha256": s.snapshot.sha256,
                        "bytes": s.snapshot.size,
                    }
                )
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

    stage = run.stage()  # this run's own output; the owned output is replaced from it at the end
    report_paths: list[str] = []
    (stage / REPORTS_DIR).mkdir()
    for n, s in enumerate(steps, start=1):
        if s.outcome == "not_run":
            continue
        doc = s.report or {"check": s.label, "outcome": s.outcome, "messages": s.messages}
        name = f"{REPORTS_DIR}/{n:02d}-{(s.kind or 'doctor')}.json"
        (stage / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "utf-8")
        report_paths.append(name)
    pack_step = next((s for s in steps if s.kind == "evidence-pack"), None)
    if pack_step is not None and pack_step.outcome == PASS and pack_step.snapshot is not None:
        (stage / EVIDENCE_DIR).mkdir()
        run.stage_snapshot(pack_step.snapshot, f"{EVIDENCE_DIR}/evidence-pack.json")
        report_paths.append(f"{EVIDENCE_DIR}/evidence-pack.json")
    if copy_inputs:
        (stage / INPUTS_DIR).mkdir()
        for n, s in enumerate(steps, start=1):
            if s.kind in ("manifest", "run-record") and s.snapshot is not None:
                target = f"{INPUTS_DIR}/{n:02d}-{s.kind}.json"
                run.stage_snapshot(s.snapshot, target)
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
            RUN_MARKER: run.marker(),
        },
        strict=strict,
        report_paths=[SUMMARY_JSON, SUMMARY_MD, *report_paths],
    )
    (stage / SUMMARY_JSON).write_text(result.to_json(), encoding="utf-8")
    (stage / SUMMARY_MD).write_text(_summary_md(result, steps, shown), encoding="utf-8")
    run.publish()
    return Outcome(result, out)
