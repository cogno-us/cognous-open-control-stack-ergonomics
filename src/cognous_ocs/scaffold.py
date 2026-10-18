"""`cognous-ocs init workflow --out DIR`: a small, synthetic, idempotent workflow scaffold.

Four managed files: the action manifest, a policy file (the control plane's frame lists), a
sample run record, and `workflow.json`, which names the other three and the signing and
redaction settings `cognous-ocs doctor` reads. All content is synthetic. Writing follows one rule:
nothing that already differs is ever replaced.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognous_ocs.cli import Outcome
from cognous_ocs.envelope import ERROR, INVALID, OCS_INTERNAL, OCS_PATHS, PASS, Result
from cognous_ocs.paths import PathRefused, confine_output

WORKFLOW_FILE = "workflow.json"
MANIFEST_FILE = "manifest.json"
POLICY_FILE = "policy.json"
RUN_RECORD_FILE = "run-record.json"
MANAGED = (MANIFEST_FILE, POLICY_FILE, RUN_RECORD_FILE, WORKFLOW_FILE)

# Redaction profiles `cognous-ocs` knows by name, and the arb redact flags each stands for.
REDACTION_PROFILES: dict[str, tuple[str, ...]] = {
    "targets": ("--targets",),
    "targets-and-output": ("--targets", "--final-output"),
    "targets-output-reasons": ("--targets", "--final-output", "--reasons"),
}
DEFAULT_REDACTION = "targets"
DEMO_ONLY = "demo-only"
SECRET_ENV = "secret-env"  # noqa: S105 - a mode name, not a credential

RUN_ID = "run-scaffold-001"
STAMP = "2026-01-01T00:00:00+00:00"


def _manifest() -> dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "manifest_id": "scaffold-workflow-v1",
        "agent_name": "Scaffold Workflow Agent",
        "agent_description": "Synthetic starter workflow: reads notes and may send a report.",
        "owner": "replace-with-your-team",
        "environment": "development",
        "default_action": "escalate",
        "tools": [
            {
                "tool_name": "notes_tool",
                "description": "Reads and searches internal notes.",
                "allowed": True,
                "external_system": None,
                "data_classification": "internal",
            },
            {
                "tool_name": "mail_tool",
                "description": "Sends a report to an external recipient.",
                "allowed": True,
                "external_system": "mail",
                "data_classification": "confidential",
            },
        ],
        "actions": [
            {
                "action_name": "notes_read",
                "tool_name": "notes_tool",
                "action_type": "read",
                "description": "Read an internal note.",
                "default_action": None,
                "authority_required": [],
                "review_requirement": {"mode": "none"},
                "reliance_requirement": {
                    "required": True,
                    "allowed_source_types": ["database"],
                    "description": "A note read is recorded as a reliance source.",
                },
                "payload_policy": {
                    "required_fields": ["note_id"],
                    "optional_fields": [],
                    "sensitive_fields": [],
                    "forbidden_fields": [],
                },
                "redaction_hints": [],
                "tags": ["notes", "read"],
                "metadata": {},
            },
            {
                "action_name": "report_send",
                "tool_name": "mail_tool",
                "action_type": "external_send",
                "description": "Send a report to an external recipient.",
                "default_action": None,
                "authority_required": [
                    {
                        "scope": "report.send.external",
                        "description": "Permission to send a report outside the organisation.",
                        "required": True,
                        "source": "replace-with-your-team",
                    }
                ],
                "review_requirement": {
                    "mode": "approval_required",
                    "reviewer_role": "replace-with-reviewer-role",
                    "reason": "Every external report needs approval before sending.",
                },
                "reliance_requirement": {
                    "required": True,
                    "allowed_source_types": ["tool", "user_input"],
                    "description": "The approved draft is recorded as a reliance source.",
                },
                "payload_policy": {
                    "required_fields": ["to", "subject", "body"],
                    "optional_fields": [],
                    "sensitive_fields": ["to", "body"],
                    "forbidden_fields": [],
                },
                "redaction_hints": [
                    {
                        "field_path": "to",
                        "reason": "The recipient must be redacted in exported records.",
                        "replacement": "[REDACTED]",
                    }
                ],
                "tags": ["report", "send", "external"],
                "metadata": {},
            },
        ],
        "metadata": {
            "synthetic": True,
            "note": "Starter file written by cognous-ocs init workflow.",
        },
    }


def _policy() -> dict[str, Any]:
    return {
        "policy_version": "scaffold-policy-0.1",
        "allowed_tools": ["notes_read"],
        "blocked_tools": ["report_send"],
        "note": "Synthetic starter policy: reads allowed, the external send blocked until "
        "authority is recorded.",
    }


def _run_record() -> dict[str, Any]:
    send_reason = "Tool 'report_send' is blocked in this frame: external-send authority is missing."
    return {
        "run_id": RUN_ID,
        "task": "Read a note and attempt to send a report.",
        "created_at": STAMP,
        "frame": {
            "frame_id": "frame-scaffold-001",
            "task": "Read a note and attempt to send a report.",
            "actor": "scaffold-workflow-agent",
            "environment": "development",
            "allowed_tools": ["notes_read"],
            "blocked_tools": ["report_send"],
            "policy_version": "scaffold-policy-0.1",
            "created_at": STAMP,
        },
        "actions": [
            {
                "action_id": "action-scaffold-001",
                "run_id": RUN_ID,
                "tool_name": "notes_read",
                "action_type": "read",
                "target": "note:N-1",
                "payload": {"note_id": "N-1"},
                "proposed_at": "2026-01-01T00:00:01+00:00",
                "reason": "Read the note the report is based on.",
            },
            {
                "action_id": "action-scaffold-002",
                "run_id": RUN_ID,
                "tool_name": "report_send",
                "action_type": "external_send",
                "target": "recipient@example.invalid",
                "payload": {"to": "recipient@example.invalid", "subject": "Report", "body": "..."},
                "proposed_at": "2026-01-01T00:00:03+00:00",
                "reason": "Send the report.",
            },
        ],
        "decisions": [
            {
                "decision_id": "decision-scaffold-001",
                "action_id": "action-scaffold-001",
                "run_id": RUN_ID,
                "result": "allow",
                "policy_name": "read_allowed_tool_policy",
                "reason": "Tool 'notes_read' is allowed and action type is 'read'.",
                "decided_at": "2026-01-01T00:00:02+00:00",
                "deterministic_fingerprint": "fp-scaffold-notes-read",
            },
            {
                "decision_id": "decision-scaffold-002",
                "action_id": "action-scaffold-002",
                "run_id": RUN_ID,
                "result": "block",
                "policy_name": "blocked_tool_policy",
                "reason": send_reason,
                "decided_at": "2026-01-01T00:00:04+00:00",
                "deterministic_fingerprint": "fp-scaffold-report-send",
            },
        ],
        "authority_records": [
            {
                "authority_id": "authority-scaffold-001",
                "run_id": RUN_ID,
                "actor": "scaffold-workflow-agent",
                "scope": ["read"],
                "expires_at": None,
                "source": "user_consent",
                "created_at": STAMP,
            }
        ],
        "reliance_records": [
            {
                "reliance_id": "reliance-scaffold-001",
                "run_id": RUN_ID,
                "source_name": "notes_read",
                "source_type": "tool",
                "scope": "note N-1",
                "referenced_action_id": "action-scaffold-001",
                "created_at": "2026-01-01T00:00:02+00:00",
            }
        ],
        "blocked_actions": [
            {
                "blocked_id": "blocked-scaffold-001",
                "action_id": "action-scaffold-002",
                "run_id": RUN_ID,
                "reason": send_reason,
                "policy_name": "blocked_tool_policy",
                "blocked_at": "2026-01-01T00:00:04+00:00",
            }
        ],
        "completed": True,
        "final_output": "Note read; report send blocked: external-send authority is missing.",
        "replay_bundle_id": None,
    }


def _workflow() -> dict[str, Any]:
    return {
        "workflow_version": "0.1",
        "manifest": MANIFEST_FILE,
        "policy": POLICY_FILE,
        "run_record": RUN_RECORD_FILE,
        "replay_signing": {"mode": DEMO_ONLY},
        "redaction": {"profile": DEFAULT_REDACTION},
    }


def render(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def managed_bytes() -> dict[str, bytes]:
    return {
        MANIFEST_FILE: render(_manifest()),
        POLICY_FILE: render(_policy()),
        RUN_RECORD_FILE: render(_run_record()),
        WORKFLOW_FILE: render(_workflow()),
    }


@dataclass
class Plan:
    created: list[str]
    unchanged: list[str]
    conflicts: list[str]


def _classify(directory: Path, name: str, content: bytes) -> str:
    """missing, same, or conflict. A non-regular file, a link or a differing file conflicts."""
    target = directory / name
    if not (target.exists() or target.is_symlink()):
        return "missing"
    if target.is_symlink() or not target.is_file() or target.stat().st_nlink > 1:
        return "conflict"
    try:
        return "same" if target.read_bytes() == content else "conflict"
    except OSError:
        return "conflict"


def plan(directory: Path) -> Plan:
    content = managed_bytes()
    result = Plan([], [], [])
    for name in MANAGED:
        state = _classify(directory, name, content[name])
        {"missing": result.created, "same": result.unchanged, "conflict": result.conflicts}[
            state
        ].append(name)
    return result


DOES_NOT_PROVE = [
    "that the scaffold fits your workflow: every name, scope and role in it is a placeholder",
    "that any policy or authority decision in it is right",
    "producer authenticity or external source linkage",
]


def run_init(out_raw: str) -> Outcome:
    command = "init workflow"
    subject: dict[str, Any] = {"kind": "workflow-scaffold", "path": out_raw}
    try:
        out = confine_output(out_raw)
    except PathRefused as exc:
        return Outcome(Result(command, INVALID, [OCS_PATHS], subject, messages=[str(exc)]))
    if out.exists() and (not out.is_dir() or out.is_symlink()):
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_PATHS],
                subject,
                messages=["output path exists and is not a directory"],
            )
        )
    planned = plan(out)
    if planned.conflicts:
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_PATHS],
                subject,
                messages=[
                    "stopped before changing anything: these managed files exist and differ "
                    f"from the scaffold (or are not plain files): {', '.join(planned.conflicts)}",
                    "nothing was replaced; move or delete them yourself to re-scaffold",
                ],
            )
        )
    content = managed_bytes()
    written: list[str] = []
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name in planned.created:
            _create_exclusive(out / name, content[name])
            written.append(name)
    except FileExistsError:
        return Outcome(
            Result(
                command,
                INVALID,
                [OCS_PATHS],
                subject,
                messages=[
                    "a managed file appeared while writing and was left untouched; "
                    f"created before stopping: {', '.join(written) or 'none'}",
                ],
            )
        )
    except OSError as exc:
        return Outcome(
            Result(
                command,
                ERROR,
                [OCS_INTERNAL],
                subject,
                messages=[f"could not write the scaffold ({type(exc).__name__})"],
            )
        )
    messages = [
        f"created: {', '.join(written) or 'none'}",
        f"unchanged: {', '.join(planned.unchanged) or 'none'}",
        "next: cognous-ocs doctor --workflow " + out_raw,
    ]
    return Outcome(
        Result(
            command,
            PASS,
            [],
            subject,
            does_not_prove=list(DOES_NOT_PROVE),
            messages=messages,
            extensions={
                "x-files": [
                    {"file": name, "status": "created" if name in written else "unchanged"}
                    for name in MANAGED
                ]
            },
        ),
        out,
    )


def _create_exclusive(path: Path, content: bytes) -> None:
    """Create `path` only if it does not exist; never truncate or replace."""
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
    )
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
