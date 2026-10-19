"""Result envelope, outcomes, exit codes and text hygiene (cli-contract sections 3 to 5)."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cognous_ocs import ENVELOPE_VERSION

# Outcomes. `error` is not one of the five in the Phase 0 draft: exit 4 had no
# outcome, so the draft is corrected (docs/cli-contract.md section 4).
PASS = "pass"  # noqa: S105 - an outcome name, not a credential
REVIEW = "review"
NOT_EVALUATED = "not_evaluated"
FAIL = "fail"
INVALID = "invalid"
ERROR = "error"

OUTCOMES = frozenset({PASS, REVIEW, NOT_EVALUATED, FAIL, INVALID, ERROR})

# Reason codes (cli-contract section 6).
OCS_VERSION = "OCS_VERSION"
OCS_PATHS = "OCS_PATHS"
OCS_KIND_UNREGISTERED = "OCS_KIND_UNREGISTERED"
OCS_ARCHIVAL_ONLY = "OCS_ARCHIVAL_ONLY"
OCS_NOT_APPLICABLE = "OCS_NOT_APPLICABLE"
OCS_INPUT_INVALID = "OCS_INPUT_INVALID"
OCS_DEMO_EXPECTATION = "OCS_DEMO_EXPECTATION"
OCS_BLOCKING_FINDING = "OCS_BLOCKING_FINDING"
OCS_INTERNAL = "OCS_INTERNAL"

DIAGNOSTICS_NAME = "ci-diagnostics.json"


def exit_code_for(outcome: str, *, strict: bool = False) -> int:
    """Map an outcome to the contract exit code.

    `review` exits 0 by default and 1 under `--strict`; `not_evaluated` exits 3.
    """
    if outcome == PASS:
        return 0
    if outcome == REVIEW:
        return 1 if strict else 0
    if outcome == FAIL:
        return 1
    if outcome == INVALID:
        return 2
    if outcome == NOT_EVALUATED:
        return 3
    return 4


def strip_controls(value: object) -> str:
    """Remove every control character (Unicode category Cc, plus line and paragraph separators)."""
    text = str(value)
    return "".join(ch for ch in text if unicodedata.category(ch) not in {"Cc", "Zl", "Zp"})


@dataclass
class Result:
    """One run's outcome. `cognous-ocs` adds no verdict of its own: verdict_source names a component."""

    command: str
    outcome: str
    reason_codes: list[str] = field(default_factory=list)
    subject: dict[str, Any] = field(default_factory=dict)
    verdict_source: dict[str, str] | None = None
    does_not_prove: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    strict: bool = False
    report_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")
        for name in self.extensions:
            if not name.startswith("x-"):
                raise ValueError(f"extension member {name!r} must start with 'x-'")

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.outcome, strict=self.strict)

    def to_dict(self) -> dict[str, Any]:
        if self.outcome == PASS and not self.does_not_prove:
            raise ValueError("a pass must state what it does not prove")
        body: dict[str, Any] = {
            "envelope_version": ENVELOPE_VERSION,
            "command": self.command,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "reason_codes": list(self.reason_codes),
            "subject": self.subject,
        }
        if self.verdict_source is not None:
            body["verdict_source"] = self.verdict_source
        body["does_not_prove"] = list(self.does_not_prove)
        body["messages"] = [strip_controls(m) for m in self.messages]
        if self.extensions:
            body["extensions"] = self.extensions
        return body

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    def to_text(self) -> str:
        lines = [f"{self.command}: {self.outcome} (exit {self.exit_code})"]
        lines += [f"  {strip_controls(m)}" for m in self.messages]
        if self.reason_codes:
            lines.append("  reason codes: " + ", ".join(self.reason_codes))
        if self.verdict_source is not None:
            vs = self.verdict_source
            lines.append(
                f"  verdict source: {vs['component']} ({vs['contract']} {vs['contract_version']})"
            )
        if self.does_not_prove:
            lines.append("  does not prove: " + "; ".join(self.does_not_prove))
        return "\n".join(lines) + "\n"

    def diagnostics(self) -> dict[str, Any]:
        """The ci-diagnostics.json body.

        No artifact content, no paths outside the output directory, no secrets.
        """
        return {
            "envelope_version": ENVELOPE_VERSION,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "reason_codes": list(self.reason_codes),
            "report_paths": list(self.report_paths),
        }


def write_diagnostics(result: Result, directory: Path) -> Path:
    """Write ci-diagnostics.json into an already-confined directory.

    The directory is opened without following a final link, the content goes to a new
    exclusive file inside it, and an atomic rename puts it at the diagnostics name. A link
    already sitting at that name is replaced as a link; its target is never opened.
    """
    directory.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(result.diagnostics(), indent=2) + "\n").encode("utf-8")
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        temp = f".{DIAGNOSTICS_NAME}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o644)
                handle.write(body)
            os.replace(temp, DIAGNOSTICS_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)
    return directory / DIAGNOSTICS_NAME
