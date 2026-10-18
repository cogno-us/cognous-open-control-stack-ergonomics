"""Input and output-path confinement for the cognous-ocs composite action. Standard library only.

It runs before anything is installed or executed, and it is the only place action inputs are
read. Every input arrives through the environment (`OCS_IN_*`), never interpolated into a shell
script. It refuses (exit 2) and writes nothing outside the confined output directory when:

- any input contains CR, LF or NUL;
- the output directory is outside the workspace and the runner temp directory, is one of those
  roots, goes through a symlink, or has characters that would act as an upload glob;
- an input file or directory is missing, lives inside the output directory, or contains it;
- neither a strict evidence input nor the explicit non-assurance mode is chosen, or both are;
- an enumerated or numeric input is out of range.

Messages are fixed text: an input value is never echoed (workflow-command injection).
The path rules mirror `cognous_ocs.paths`; tests/test_action.py runs both on the same cases.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

OWNED = {"ci-summary.json", "ci-summary.md", "ci-diagnostics.json", "reports", "evidence", "inputs"}
REPORT_MODES = ("full", "fail-fast")
INSTALL_MODES = ("source", "preinstalled")
BOOLS = ("true", "false")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9_./+@ =:-]+$")
FIELDS = (
    "MANIFEST",
    "WORKFLOW",
    "EVIDENCE_PACK",
    "NON_ASSURANCE",
    "STRICT",
    "OUT_DIR",
    "REPORT_MODE",
    "UPLOAD_REPORTS",
    "UPLOAD_FULL",
    "RETENTION_DAYS",
    "UPLOAD_NAME",
    "INSTALL",
)
DEFAULTS = {
    "NON_ASSURANCE": "false",
    "STRICT": "false",
    "OUT_DIR": "ocs-out",
    "REPORT_MODE": "full",
    "UPLOAD_REPORTS": "true",
    "UPLOAD_FULL": "false",
    "RETENTION_DAYS": "14",
    "UPLOAD_NAME": "ocs-reports",
    "INSTALL": "source",
}


class Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _bad_text(value: str) -> bool:
    return any(c in value for c in ("\r", "\n", "\x00"))


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def roots(workspace: Path, runner_temp: Path | None) -> list[Path]:
    found = [Path(os.path.normpath(workspace))]
    if runner_temp is not None:
        found.append(Path(os.path.normpath(runner_temp)))
    return found


def confine_output(raw: str, workspace: Path, runner_temp: Path | None) -> Path:
    """The absolute output directory, or Refused. Same rules as cognous_ocs.paths.confine_output."""
    if _bad_text(raw) or not raw:
        raise Refused("OCS_PATHS", "output path is empty or contains a CR, LF or NUL character")
    base = Path(os.path.normpath(workspace))
    candidate = Path(raw)
    absolute = Path(os.path.normpath(candidate if candidate.is_absolute() else base / candidate))
    for root in roots(workspace, runner_temp):
        if not _inside(absolute, root):
            continue
        if absolute == root:
            raise Refused("OCS_PATHS", "output path is a workspace or temp root itself")
        walker = root
        for part in absolute.relative_to(root).parts:
            walker = walker / part
            if walker.is_symlink():
                raise Refused("OCS_PATHS", "output path involves a symlink")
        if not _inside(absolute.resolve(), root.resolve()):
            raise Refused("OCS_PATHS", "output path resolves outside its root")
        if not SAFE_PATH.match(str(absolute)):
            raise Refused("OCS_PATHS", "output path has characters that are not allowed")
        if absolute.exists() and not absolute.is_dir():
            raise Refused("OCS_PATHS", "output path exists and is not a directory")
        return absolute
    raise Refused("OCS_PATHS", "output path is outside the workspace and the runner temp directory")


def _check_input_path(raw: str, out: Path, workspace: Path, *, directory: bool) -> None:
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    if not (path.is_dir() if directory else path.is_file()):
        raise Refused("OCS_PATHS", "an input path is missing or not the expected kind")
    resolved = path.resolve()
    out_resolved = out.resolve()
    if _inside(resolved, out_resolved):
        raise Refused(
            "OCS_PATHS", "an input aliases an owned output (it is inside the output directory)"
        )
    if directory and _inside(out_resolved, resolved):
        raise Refused("OCS_PATHS", "the output directory is inside an input directory")


def plan(env: dict[str, str], workspace: Path, runner_temp: Path | None) -> dict[str, str]:
    """Validate every input; return the confined values the action may use."""
    values = {f: env.get(f"OCS_IN_{f}", DEFAULTS.get(f, "")) for f in FIELDS}
    values = {k: (v if v != "" or k not in DEFAULTS else DEFAULTS[k]) for k, v in values.items()}
    if any(_bad_text(v) for v in values.values()):
        raise Refused("OCS_PATHS", "an input contains a CR, LF or NUL character")
    out = confine_output(values["OUT_DIR"], workspace, runner_temp)
    if out.exists() and any(child.name not in OWNED for child in out.iterdir()):
        raise Refused("OCS_PATHS", "the output directory holds files cognous-ocs ci does not own")
    for key, allowed in (
        ("REPORT_MODE", REPORT_MODES),
        ("INSTALL", INSTALL_MODES),
        ("NON_ASSURANCE", BOOLS),
        ("STRICT", BOOLS),
        ("UPLOAD_REPORTS", BOOLS),
        ("UPLOAD_FULL", BOOLS),
    ):
        if values[key] not in allowed:
            raise Refused(
                "OCS_INPUT_INVALID",
                f"input {key.lower().replace('_', '-')} is not one of its allowed values",
            )
    if not values["RETENTION_DAYS"].isdigit() or not 1 <= int(values["RETENTION_DAYS"]) <= 90:
        raise Refused("OCS_INPUT_INVALID", "retention-days must be a whole number from 1 to 90")
    if not NAME.match(values["UPLOAD_NAME"]):
        raise Refused("OCS_INPUT_INVALID", "upload-name has characters that are not allowed")
    # Fail-closed profile: a strict evidence input, or the explicit non-assurance mode, not both.
    strict_evidence = values["EVIDENCE_PACK"] != ""
    non_assurance = values["NON_ASSURANCE"] == "true"
    if strict_evidence == non_assurance:
        raise Refused(
            "OCS_INPUT_INVALID",
            "refusing to run: choose exactly one of a strict evidence input (evidence-pack) "
            "or the explicit non-assurance mode (non-assurance: true)",
        )
    if not (values["MANIFEST"] or values["WORKFLOW"] or strict_evidence):
        raise Refused("OCS_INPUT_INVALID", "nothing to check: give manifest or workflow")
    for key, directory in (("MANIFEST", False), ("WORKFLOW", True), ("EVIDENCE_PACK", False)):
        if values[key]:
            _check_input_path(values[key], out, workspace, directory=directory)
    values["OUT_DIR"] = str(out)
    return values


def write_diagnostics(out: Path, code: str) -> None:
    """Same write as cognous_ocs.envelope.write_diagnostics: no final link followed, atomic replace."""
    out.mkdir(parents=True, exist_ok=True)
    body = {
        "envelope_version": "1",
        "outcome": "invalid",
        "exit_code": 2,
        "reason_codes": [code],
        "report_paths": [],
    }
    data = (json.dumps(body, indent=2) + "\n").encode("utf-8")
    name = "ci-diagnostics.json"
    dir_fd = os.open(out, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        temp = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o644)
                handle.write(data)
            os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)


def emit(values: dict[str, str], output_file: str | None) -> None:
    lines = {
        "out-dir": values["OUT_DIR"],
        "report-mode": values["REPORT_MODE"],
        "retention-days": values["RETENTION_DAYS"],
        "upload-name": values["UPLOAD_NAME"],
        "upload-reports": values["UPLOAD_REPORTS"],
        "upload-full": values["UPLOAD_FULL"],
    }
    text = "".join(f"{k}={v}\n" for k, v in lines.items())
    if output_file:
        with open(output_file, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    workspace = Path(env.get("GITHUB_WORKSPACE") or os.getcwd())
    temp = Path(env["RUNNER_TEMP"]) if env.get("RUNNER_TEMP") else None
    try:
        values = plan(env, workspace, temp)
    except Refused as exc:
        sys.stderr.write(f"::error title=cognous-ocs action::{exc.message}\n")
        # Leave a diagnostics file only where the output directory itself was acceptable.
        try:
            out = confine_output(env.get("OCS_IN_OUT_DIR") or DEFAULTS["OUT_DIR"], workspace, temp)
            if not out.exists() or all(c.name in OWNED for c in out.iterdir()):
                write_diagnostics(out, exc.code)
        except (Refused, OSError):
            pass
        return 2
    emit(values, env.get("GITHUB_OUTPUT"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
