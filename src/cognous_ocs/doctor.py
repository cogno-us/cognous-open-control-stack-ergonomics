"""`cognous-ocs doctor [--workflow DIR]`: read-only setup diagnosis.

Strictly read-only: it never runs the agent or a component, never writes, and never touches the
network. It reads files and the process environment and reports PASS, FAIL or SKIP per check,
each with a stable code, a short message and one action. It validates nothing (that is
`cognous-ocs validate`) and decides nothing about authority; it checks that the pieces a workflow needs
are present, named consistently and confined to the workflow directory.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognous_ocs import registry
from cognous_ocs.cli import Outcome
from cognous_ocs.demo import ComponentProblem, installed_commit
from cognous_ocs.envelope import (
    INVALID,
    PASS,
    Result,
)
from cognous_ocs.lock import COMPONENTS
from cognous_ocs.runner import check_pins
from cognous_ocs.scaffold import DEMO_ONLY, REDACTION_PROFILES, SECRET_ENV, WORKFLOW_FILE
from cognous_ocs.validate import load_document

OK = "PASS"
FAILED = "FAIL"
SKIPPED = "SKIP"

CHECK_CODES = (
    "OCS_VERSION",
    "OCS_MANIFEST",
    "OCS_POLICY_BIND",
    "OCS_PATHS",
    "OCS_SIGNING",
    "OCS_REDACTION",
)
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
NO_AUTHORITY_NEEDED = frozenset({"read"})

DOES_NOT_PROVE = [
    "that any artifact validates: doctor reads files and runs no validator (use cognous-ocs validate)",
    "that the workflow is correct, safe or authorised to run",
    "producer authenticity or external source linkage",
]


@dataclass
class Check:
    code: str
    status: str
    message: str
    action: str = "none"

    def record(self) -> dict[str, str]:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "action": self.action,
        }

    def lines(self) -> list[str]:
        head = f"{self.status:<4} {self.code}: {self.message}"
        return [head] if self.status == OK else [head, f"     action: {self.action}"]


def _skip(code: str, why: str, action: str) -> Check:
    return Check(code, SKIPPED, why, action)


def _bad_path_text(raw: str) -> bool:
    return any(c in raw for c in ("\r", "\n", "\x00"))


def _plain_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _confined(base: Path, raw: object) -> tuple[Path | None, str]:
    """Resolve a workflow-relative reference, or explain why it is refused."""
    if not isinstance(raw, str) or not raw:
        return None, "is not a non-empty string"
    if _bad_path_text(raw):
        return None, "contains a CR, LF or NUL character"
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        return None, "is absolute or climbs out of the workflow directory"
    walker = base
    for part in rel.parts:
        walker = walker / part
        if walker.is_symlink():
            return None, "goes through a symlink"
    return base / rel, ""


def _read(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not _plain_file(path):
        return None, "is missing or not a plain file"
    try:
        return load_document(path), ""
    except (OSError, UnicodeDecodeError, ValueError):
        return None, "is not one strict JSON object"


@dataclass
class Workflow:
    base: Path
    settings: dict[str, Any] | None = None
    manifest_path: Path | None = None
    policy_path: Path | None = None
    manifest: dict[str, Any] | None = None


def check_version(bin_dir: Path | None, commit_of: Callable[[str], str | None]) -> Check:
    try:
        check_pins(COMPONENTS, bin_dir, commit_of)
    except ComponentProblem as exc:
        return Check(
            "OCS_VERSION",
            FAILED,
            str(exc).split(". Install with")[0],
            "install the pinned components: "
            "pip install 'cognous-ocs[stack]' (from this repository)",
        )
    return Check("OCS_VERSION", OK, "all four components are installed at their pinned commits")


def check_paths(wf: Workflow) -> Check:
    action = f"run: cognous-ocs init workflow --out {wf.base} (on an empty directory), or fix {WORKFLOW_FILE}"
    if wf.settings is None:
        return Check("OCS_PATHS", FAILED, f"{WORKFLOW_FILE} is missing or not strict JSON", action)
    problems: list[str] = []
    for key in ("manifest", "policy", "run_record"):
        if key not in wf.settings:
            problems.append(f"{WORKFLOW_FILE} does not name '{key}'")
            continue
        path, why = _confined(wf.base, wf.settings[key])
        if path is None:
            problems.append(f"'{key}' {why}")
            continue
        if key == "manifest":
            wf.manifest_path = path
        elif key == "policy":
            wf.policy_path = path
        if not _plain_file(path):  # named and confined, but there is nothing to validate
            problems.append(f"'{key}' is missing or not a plain file")
    if problems:
        return Check("OCS_PATHS", FAILED, "; ".join(problems), f"fix the paths in {WORKFLOW_FILE}")
    return Check(
        "OCS_PATHS", OK, "manifest, policy and run record are plain files inside the workflow"
    )


def _action_problems(doc: dict[str, Any]) -> list[str]:
    actions = doc.get("actions")
    if not isinstance(actions, list) or not actions:
        return ["the manifest declares no actions"]
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(actions):
        name = item.get("action_name") if isinstance(item, dict) else None
        label = name if isinstance(name, str) and name else f"actions[{index}]"
        if not isinstance(item, dict) or not isinstance(name, str) or not name:
            problems.append(f"{label} has no action_name")
            continue
        if name in seen:
            problems.append(f"{label} is declared twice")
        seen.add(name)
        kind = item.get("action_type")
        if not isinstance(kind, str) or not kind:
            problems.append(f"{label} has no action_type")
            continue
        authority = item.get("authority_required")
        if not isinstance(authority, list):
            problems.append(f"{label} has no authority_required list")
        elif kind not in NO_AUTHORITY_NEEDED and not authority:
            problems.append(f"{label} ({kind}) has an empty authority_required")
    return problems


def check_manifest(wf: Workflow) -> Check:
    action = "run: cognous-ocs validate MANIFEST --kind manifest, then add the missing declarations"
    if wf.manifest_path is None:
        return _skip(
            "OCS_MANIFEST", "the manifest path was refused (see OCS_PATHS)", "fix OCS_PATHS first"
        )
    doc, why = _read(wf.manifest_path)
    if doc is None:
        return Check("OCS_MANIFEST", FAILED, f"the manifest {why}", action)
    wf.manifest = doc
    version = registry.declared_version("manifest", doc)
    entry = registry.lookup("manifest", version)
    if entry is None or entry.mode != registry.FULL:
        return Check(
            "OCS_MANIFEST",
            FAILED,
            f"manifest version {version!r} is not a fully registered version",
            action,
        )
    problems = _action_problems(doc)
    if problems:
        shown = "; ".join(problems[:4]) + (
            f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""
        )
        return Check("OCS_MANIFEST", FAILED, shown, action)
    count = len(doc["actions"])
    return Check(
        "OCS_MANIFEST",
        OK,
        f"the manifest parses and its {count} actions carry a type and authority requirement",
    )


def _name_list(doc: dict[str, Any], key: str) -> list[str] | None:
    value = doc.get(key)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def check_policy_bind(wf: Workflow) -> Check:
    action = "add each uncovered action to allowed_tools or blocked_tools in the policy file"
    if wf.policy_path is None:
        return _skip(
            "OCS_POLICY_BIND", "the policy path was refused (see OCS_PATHS)", "fix OCS_PATHS first"
        )
    if wf.manifest is None or any(
        isinstance(a, dict) is False for a in wf.manifest.get("actions", [])
    ):
        return _skip(
            "OCS_POLICY_BIND",
            "the manifest is not usable (see OCS_MANIFEST)",
            "fix OCS_MANIFEST first",
        )
    policy, why = _read(wf.policy_path)
    if policy is None:
        return Check("OCS_POLICY_BIND", FAILED, f"the policy file {why}", action)
    allowed = _name_list(policy, "allowed_tools")
    blocked = _name_list(policy, "blocked_tools")
    if allowed is None or blocked is None:
        return Check(
            "OCS_POLICY_BIND",
            FAILED,
            "the policy needs allowed_tools and blocked_tools as lists of names",
            action,
        )
    names = [
        a["action_name"] for a in wf.manifest["actions"] if isinstance(a.get("action_name"), str)
    ]
    uncovered = [n for n in names if n not in allowed and n not in blocked]
    both = sorted(set(allowed) & set(blocked))
    problems: list[str] = []
    if uncovered:
        problems.append("no policy rule covers: " + ", ".join(uncovered))
    if both:
        problems.append("allowed and blocked at once: " + ", ".join(both))
    if problems:
        return Check("OCS_POLICY_BIND", FAILED, "; ".join(problems), action)
    return Check(
        "OCS_POLICY_BIND",
        OK,
        f"every manifest action ({len(names)}) is covered by exactly one policy list",
    )


def check_signing(wf: Workflow, env: Mapping[str, str]) -> Check:
    action = (
        f"set replay_signing in {WORKFLOW_FILE} to mode '{DEMO_ONLY}', or to mode "
        f"'{SECRET_ENV}' with the name of an environment variable"
    )
    if wf.settings is None:
        return _skip("OCS_SIGNING", f"no readable {WORKFLOW_FILE}", "fix OCS_PATHS first")
    cfg = wf.settings.get("replay_signing")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("mode"), str):
        return Check(
            "OCS_SIGNING",
            FAILED,
            "replay signing is not configured and not marked demo-only",
            action,
        )
    if cfg["mode"] == DEMO_ONLY:
        return Check(
            "OCS_SIGNING",
            OK,
            "replay signing is marked demo-only (a demo signature says nothing about a producer)",
        )
    if cfg["mode"] == SECRET_ENV:
        name = cfg.get("env")
        if not isinstance(name, str) or not ENV_NAME.match(name):
            return Check(
                "OCS_SIGNING", FAILED, "secret-env signing needs an env variable name", action
            )
        if not env.get(name):
            return Check(
                "OCS_SIGNING",
                FAILED,
                f"the signing secret variable {name} is not set in this environment",
                f"export {name} in the environment that signs replay bundles (never commit it)",
            )
        return Check(
            "OCS_SIGNING",
            OK,
            f"the signing secret variable {name} is set (value not read into any output)",
        )
    return Check("OCS_SIGNING", FAILED, "replay signing mode is not recognised", action)


def check_redaction(wf: Workflow) -> Check:
    action = f"set redaction.profile in {WORKFLOW_FILE} to one of: {', '.join(REDACTION_PROFILES)}"
    if wf.settings is None:
        return _skip("OCS_REDACTION", f"no readable {WORKFLOW_FILE}", "fix OCS_PATHS first")
    cfg = wf.settings.get("redaction")
    profile = cfg.get("profile") if isinstance(cfg, dict) else None
    if not isinstance(profile, str) or not profile:
        return Check("OCS_REDACTION", FAILED, "no redaction profile is named", action)
    if profile not in REDACTION_PROFILES:
        return Check(
            "OCS_REDACTION",
            FAILED,
            "the named redaction profile is not one cognous-ocs can load",
            action,
        )
    return Check("OCS_REDACTION", OK, f"redaction profile '{profile}' is named and loadable")


def run_checks(
    workflow_raw: str | None,
    *,
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
    env: Mapping[str, str] | None = None,
) -> list[Check]:
    checks = [check_version(bin_dir, commit_of)]
    needs = "pass --workflow DIR to check a workflow"
    if workflow_raw is None:
        return checks + [_skip(c, "no --workflow given", needs) for c in CHECK_CODES[1:]]
    if _bad_path_text(workflow_raw) or not Path(workflow_raw).is_dir():
        refusal = Check(
            "OCS_PATHS",
            FAILED,
            "the workflow path is not a readable directory (or contains CR, LF or NUL)",
            "run: cognous-ocs init workflow --out DIR, then cognous-ocs doctor --workflow DIR",
        )
        rest = [
            _skip(c, "the workflow directory was refused (see OCS_PATHS)", "fix OCS_PATHS first")
            for c in ("OCS_MANIFEST", "OCS_POLICY_BIND", "OCS_SIGNING", "OCS_REDACTION")
        ]
        return [checks[0], rest[0], rest[1], refusal, rest[2], rest[3]]
    base = Path(workflow_raw)
    settings, _why = _read(base / WORKFLOW_FILE)
    wf = Workflow(base, settings)
    paths = check_paths(wf)
    manifest = check_manifest(wf)
    return [
        checks[0],
        manifest,
        check_policy_bind(wf),
        paths,
        check_signing(wf, os.environ if env is None else env),
        check_redaction(wf),
    ]


def run_doctor(
    workflow_raw: str | None,
    *,
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
    env: Mapping[str, str] | None = None,
) -> Outcome:
    subject: dict[str, Any] = {"kind": "setup", "path": workflow_raw or "(no workflow)"}
    checks = run_checks(workflow_raw, bin_dir=bin_dir, commit_of=commit_of, env=env)
    failed = [c for c in checks if c.status == FAILED]
    extensions = {"x-checks": [c.record() for c in checks]}
    lines = [line for c in checks for line in c.lines()]
    if failed:
        return Outcome(
            Result(
                "doctor",
                INVALID,
                [c.code for c in failed],
                subject,
                messages=lines,
                extensions=extensions,
            ),
            read_only=True,
        )
    return Outcome(
        Result(
            "doctor",
            PASS,
            [],
            subject,
            does_not_prove=list(DOES_NOT_PROVE),
            messages=[*lines, "ready: no check failed"],
            extensions=extensions,
        ),
        read_only=True,
    )
