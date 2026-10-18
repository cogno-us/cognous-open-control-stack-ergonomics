"""`ocs demo customer-service`: the bundled example, run end to end, offline.

Every layer is run by the real component command in a subprocess: declare (aam), control
(`acp run`, then `acp validate-run` on what it wrote), replay (arb) and evidence (agep). `ocs`
never imports component code. It asserts each exit code, reads the artifacts the components
produced, and succeeds only if the blocked email send is evidenced in every layer. It decides
nothing itself: the held send is `acp run` exit 1, which the demo maps to its expectation, and the
component's reason codes are passed through unchanged.
"""

from __future__ import annotations

import contextlib
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ocs import EXAMPLE_NAME, bind, render
from ocs.cli import Outcome
from ocs.envelope import (
    ERROR,
    FAIL,
    INVALID,
    OCS_BLOCKING_FINDING,
    OCS_DEMO_EXPECTATION,
    OCS_INTERNAL,
    OCS_PATHS,
    OCS_VERSION,
    PASS,
    Result,
    strip_controls,
    write_diagnostics,
)
from ocs.fixtures_data import (
    ACP_RUN_FILES,
    FixtureIntegrityError,
    read_acp_run_input,
    read_fixture,
)
from ocs.lock import COMPONENTS
from ocs.paths import PathRefused, confine_output

MARKER = ".ocs-demo-output"
# Public, intentionally not secret: the replay signature in this demo proves nothing about a producer.
DEMO_SECRET = "ocs-demo-only-not-a-secret"  # noqa: S105
WRONG_SECRET = "wrong-key"  # noqa: S105
KEYS = {DEMO_SECRET, WRONG_SECRET}
STEP_TIMEOUT_SECONDS = 60.0
LOG_LIMIT = 20_000

# The fixed evaluation time the bundled acp-run inputs are written for (see provenance.json).
ACP_RUN_NOW = "2026-10-05T19:00:00Z"
# The templates the demo re-keys to the real runs; the legacy run-record fixture is not used here.
TEMPLATES = ("manifest.json", "replay-bundle.json", "evidence-pack.json")
# What the scenario expects of each control-plane run: exit 0 applied, exit 1 the blocking result.
CONTROL_EXPECTED = {"crm_read": 0, "notes_search": 0, "email_send": 1}
EXPECTED_SEND_CODES = ["grant_scope_mismatch"]
CONTROL_CONTRACT = "bounded-authorization-effect"

DOES_NOT_PROVE = [
    "institutional authority: the control plane's resolver is synthetic (declared in a bundled "
    "file, not authenticated), so the grant and its status are the demo's own inputs",
    "a real-world effect: acp run applies to a local file destination, not a mail system or CRM",
    "that run-record.json is more than a projection of the control plane's native "
    "bounded-run-record.json",
    "that the replay bundle and evidence pack were produced by the control plane: they are "
    "synthetic templates that ocs re-keyed to the ids and reason codes of the real runs",
    "that any real agent action was blocked, or that real customer data was involved",
    "producer authenticity: the replay signature uses a public demo-only key",
    "external source linkage",
    "that the pinned component versions are free of defects, or that newer versions behave the same",
    "anything about deployment or release status",
]


class ComponentProblem(Exception):
    """A pinned component is missing or does not match its pin."""


class StepRunError(Exception):
    """A step could not be executed (timeout or OS error); maps to exit 4."""


class StepFailed(Exception):
    """A step ran but its exit code was not the expected one; the demo stops there."""


@dataclass
class Step:
    layer: str
    component: str
    command: str
    expected: int
    exit_code: int | None = None
    ok: bool = False

    def record(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "component": self.component,
            "command": self.command,
            "expected_exit": self.expected,
            "exit": self.exit_code,
            "ok": self.ok,
        }


@dataclass
class Run:
    out: Path
    bin_dir: Path
    timeout: float
    steps: list[Step] = field(default_factory=list)
    facts: dict[str, bool] = field(default_factory=dict)
    control: list[dict[str, Any]] = field(default_factory=list)

    def exec(self, layer: str, command: str, args: list[str], expected: int = 0) -> Step:
        exe = self.bin_dir / command
        shown = " ".join([command, *("<key>" if a in KEYS else self.rel(a) for a in args)])
        step = Step(layer, COMPONENTS[command].key, shown, expected)
        self.steps.append(step)
        env = {
            "PATH": str(self.bin_dir),
            "LANG": "C.UTF-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(self.out),
        }
        try:
            proc = subprocess.run(
                [str(exe), *args],
                cwd=self.out,
                env=env,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StepRunError(f"{shown}: could not run ({type(exc).__name__})") from exc
        step.exit_code = proc.returncode
        step.ok = proc.returncode == expected
        text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        log = self.out / "logs" / f"{len(self.steps):02d}-{command}.txt"
        header = f"$ {shown}\n(exit {proc.returncode}, expected {expected})\n\n"
        body = "".join(ch if ch == "\n" else strip_controls(ch) for ch in text[:LOG_LIMIT])
        log.write_text(header + body, encoding="utf-8")
        if not step.ok:
            raise StepFailed(shown)
        return step

    def rel(self, value: str) -> str:
        try:
            return str(Path(value).relative_to(self.out))
        except ValueError:
            return value if not value.startswith(str(self.out)) else Path(value).name


def installed_commit(distribution: str) -> str | None:
    """The git commit a distribution was installed from, per its PEP 610 record."""
    try:
        raw = md.distribution(distribution).read_text("direct_url.json")
    except md.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        info = json.loads(raw)
        commit = info["vcs_info"]["commit_id"]
    except (ValueError, KeyError, TypeError):
        return None
    return commit if isinstance(commit, str) else None


def resolve_components(
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
) -> Path:
    """Find the four component commands next to this interpreter and check their pins.

    Fails closed: a missing command, or a distribution not installed from the pinned
    commit, is a version problem (exit 2), never a pass.
    """
    directory = bin_dir or Path(sys.executable).parent
    problems: list[str] = []
    for comp in COMPONENTS.values():
        exe = directory / comp.command
        if not (exe.is_file() and os.access(exe, os.X_OK)):
            problems.append(f"{comp.command} ({comp.distribution}) is not installed")
            continue
        found = commit_of(comp.distribution)
        if found != comp.sha:
            shown = found[:12] if found else "unknown source"
            problems.append(f"{comp.distribution} is at {shown}, expected {comp.sha[:12]}")
    if problems:
        raise ComponentProblem(
            "; ".join(problems)
            + ". Install with: pip install 'cognous-ocs[stack]' (from this repository)"
        )
    return directory


def _component_version(distribution: str) -> str:
    try:
        return md.version(distribution)
    except md.PackageNotFoundError:
        return "unknown"


def _prepare_out(out: Path, *, clean: bool) -> None:
    if out.exists():
        if not out.is_dir() or out.is_symlink():
            raise PathRefused("output path exists and is not a directory")
        entries = list(out.iterdir())
        if entries:
            if not clean:
                raise PathRefused(
                    "output directory is not empty; pass --clean to replace a previous demo"
                )
            if not (out / MARKER).is_file():
                raise PathRefused(
                    "--clean only replaces a directory created by a previous ocs demo (marker missing)"
                )
            for entry in entries:
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                else:
                    shutil.rmtree(entry)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text(
        "created by ocs demo; --clean may replace this directory\n", encoding="utf-8"
    )
    for sub in ("inputs", "outputs", "logs", "control"):
        (out / sub).mkdir()


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _by(items: Any, key: str) -> dict[str, dict[str, Any]]:
    return {str(i.get(key)): i for i in items if isinstance(i, dict)}


def _read_operations(run: Run) -> dict[str, bind.Operation]:
    ops: dict[str, bind.Operation] = {}
    for name in CONTROL_EXPECTED:
        out_dir = run.out / "control" / name / "out"
        ops[name] = bind.Operation(
            name=name,
            envelope=_load(out_dir / "result.json"),
            record=_load(out_dir / "run-record.json"),
            record_path=f"control/{name}/out/run-record.json",
        )
    return ops


def _codes(text: Any) -> list[str]:
    return [c.strip() for c in str(text).split(";") if c.strip()]


def check_facts(run: Run, ops: dict[str, bind.Operation]) -> None:
    """Read the produced artifacts and record which expected facts hold."""
    inp = run.out / "inputs"
    outs = run.out / "outputs"
    facts = run.facts
    manifest = _load(inp / "manifest.json")
    bounded = _load(inp / "acp-run" / "manifest.json")
    bundle = _load(outs / "replay-bundle.json")
    pack = _load(outs / "evidence-pack.json")

    declared = _by(manifest.get("actions", []), "action_name").get("email_send", {})
    facts["declare: email_send is declared as an external send needing authority"] = declared.get(
        "action_type"
    ) == "external_send" and any(a.get("required") for a in declared.get("authority_required", []))
    bounded_send = _by(bounded.get("actions", []), "action_name").get("email_send", {})
    facts["declare: the bounded manifest requires outbound.send for email_send"] = any(
        a.get("scope") == "outbound.send" and a.get("required")
        for a in bounded_send.get("authority_required", [])
    )

    send = ops[bind.SEND]
    reads = [ops[n] for n in ("crm_read", "notes_search")]
    facts["control: crm_read and notes_search are allowed and applied by acp run (exit 0)"] = all(
        o.envelope.get("outcome") == "pass"
        and o.decision.get("result") == "allow"
        and not o.blocked
        and o.envelope["extensions"]["x-acp-run"].get("destination_effects") == 1
        for o in reads
    )
    facts["control: email_send is held by acp run (exit 1, the expected blocking result)"] = (
        send.envelope.get("outcome") == "fail"
        and send.envelope.get("exit_code") == 1
        and send.decision.get("result") == "block"
    )
    facts["control: nothing was applied for the held send"] = (
        send.envelope["extensions"]["x-acp-run"].get("destination_effects") == 0
    )
    send_action = _by(bounded.get("actions", []), "action_name").get("email_send", {})
    row = send.blocked[0] if len(send.blocked) == 1 else {}
    facts["control: a blocked_actions row names the email_send / outbound.send operation"] = (
        bool(row)
        and row.get("action_id") == send_action.get("action_id") == send.action.get("action_id")
        and "outbound.send" in str(send.action.get("reason", ""))
        and send.action.get("target") == "customer-placeholder@example.com"
        and bind.SEND not in {o.name for o in reads if o.blocked}
    )
    facts["control: the component's reason codes are passed through unchanged"] = (
        send.reason_codes
        == _codes(send.decision.get("reason"))
        == _codes(row.get("reason"))
        == EXPECTED_SEND_CODES
    )
    facts["control: all three runs share one run_id"] = (
        len({str(o.record.get("run_id")) for o in ops.values()}) == 1
    )

    replay_blocked = _by(bundle.get("blocked_actions", []), "action_id")
    send_id = str(send.action.get("action_id"))
    facts["replay: the replay bundle carries the same blocked send as the run record"] = (
        send_id in replay_blocked
        and replay_blocked[send_id].get("blocked_id") == row.get("blocked_id")
        and replay_blocked[send_id].get("reason") == row.get("reason")
        and bundle.get("run_id") == send.record.get("run_id")
    )
    redacted_text = (outs / "redacted-replay-bundle.json").read_text(encoding="utf-8")
    original_target = str(send.action.get("target"))
    facts["replay: target redaction removed the recipient from the redacted bundle"] = (
        bool(original_target) and original_target not in redacted_text
    )
    signed = _load(outs / "signed-replay-bundle.json")
    facts["replay: the signed bundle carries a signature"] = bool(
        signed.get("signature_metadata", {}).get("signature")
    )

    pack_blocked = _by(pack.get("blocked_actions", []), "action_name").get("email_send", {})
    facts["evidence: the pack records the email_send block with the component's reason code"] = (
        int(pack_blocked.get("count", 0)) >= 1
        and all(c in str(pack_blocked.get("reason_summary")) for c in send.reason_codes)
        and str(pack_blocked.get("evidence_reference", "")).endswith(f"#{row.get('blocked_id')}")
    )
    facts["evidence: the pack lists the replay bundle"] = bundle.get("bundle_id") in {
        b.get("bundle_id") for b in pack.get("replay_bundles", [])
    }
    page = (run.out / "evidence.html").read_text(encoding="utf-8")
    facts["evidence: the HTML view shows the blocked email_send with its reason code"] = (
        'chip blocked">blocked' in page
        and "<code>email_send</code>" in page
        and all(c in page for c in send.reason_codes)
    )


def control_summary(ops: dict[str, bind.Operation]) -> list[dict[str, Any]]:
    """What each acp run said, for the result's x-control extension. Component values unchanged."""
    summary = []
    for name, op in ops.items():
        ext = op.envelope.get("extensions", {}).get("x-acp-run", {})
        summary.append(
            {
                "operation": name,
                "exit": op.envelope.get("exit_code"),
                "outcome": op.envelope.get("outcome"),
                "reason_codes": op.reason_codes,
                "decision": op.decision.get("result"),
                "effect_id": ext.get("effect_id"),
                "destination_effects": ext.get("destination_effects"),
                "run_record": op.record_path,
            }
        )
    return summary


def execute(run: Run) -> None:
    """Run every step. Raises StepRunError if a step cannot be executed at all."""
    inp, outs = run.out / "inputs", run.out / "outputs"
    acp_in = inp / "acp-run"
    manifest = str(inp / "manifest.json")
    bundle_template, pack_template = inp / "replay-bundle.json", inp / "evidence-pack.json"
    signed = str(outs / "signed-replay-bundle.json")
    redacted = str(outs / "redacted-replay-bundle.json")
    tampered = outs / "tampered-signed-replay-bundle.json"

    run.exec("declare", "aam", ["validate", manifest])
    run.exec("declare", "aam", ["list-actions", manifest])
    run.exec("declare", "aam", ["validate", str(acp_in / "manifest.json")])
    # Control: the real bounded workflow, once per operation, on a throwaway store. Exit 1 is the
    # control plane's blocking result; the demo expects it for the send and maps it to its
    # expectation. Each produced run-record.json is then checked by acp validate-run.
    for name, expected in CONTROL_EXPECTED.items():
        base = run.out / "control" / name
        base.mkdir(parents=True)
        run.exec(
            "control",
            "acp",
            [
                "run",
                "--manifest",
                str(acp_in / "manifest.json"),
                "--proposal",
                str(acp_in / f"proposal-{name.replace('_', '-')}.json"),
                "--authority-context",
                str(acp_in / "authority-context.json"),
                "--resolver-state",
                str(acp_in / "resolver-state.json"),
                "--store-dir",
                str(base / "store"),
                "--out-dir",
                str(base / "out"),
                "--now",
                ACP_RUN_NOW,
                "--format",
                "json",
            ],
            expected=expected,
        )
        run.exec("control", "acp", ["validate-run", str(base / "out" / "run-record.json")])
    ops = _read_operations(run)
    run.control = control_summary(ops)
    # The replay bundle and evidence pack are templates re-keyed to the real runs' ids.
    bound_bundle = bind.bind_replay_bundle(_load(bundle_template), ops)
    bundle = outs / "replay-bundle.json"
    bundle.write_text(
        json.dumps(bound_bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    pack = outs / "evidence-pack.json"
    bound_pack = bind.bind_evidence_pack(
        _load(pack_template), ops, bound_bundle, "outputs/replay-bundle.json"
    )
    pack.write_text(json.dumps(bound_pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    run.exec("replay", "arb", ["validate", str(bundle)])
    run.exec("replay", "arb", ["sign", str(bundle), "--secret", DEMO_SECRET, "--out", signed])
    run.exec("replay", "arb", ["verify", signed, "--secret", DEMO_SECRET])
    run.exec("replay", "arb", ["redact", str(bundle), "--out", redacted, "--targets"])
    run.exec("replay", "arb", ["validate", redacted])
    # Negative controls: the component must reject a wrong key and an edited bundle.
    run.exec("replay", "arb", ["verify", signed, "--secret", WRONG_SECRET], expected=1)
    doc = _load(Path(signed))
    doc["replay_bundle"]["final_output"] = "edited after signing"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    run.exec("replay", "arb", ["verify", str(tampered), "--secret", DEMO_SECRET], expected=1)
    run.exec("evidence", "agep", ["validate", str(pack)])
    run.exec("evidence", "agep", ["summarize", str(pack)])
    run.exec("evidence", "agep", ["render", str(pack), "--out", str(outs / "evidence-pack.md")])
    loaded = render.load_pack(pack)
    (run.out / "evidence.html").write_bytes(render.render_html(loaded.data).encode("utf-8"))
    check_facts(run, ops)


def run_demo(
    out_raw: str,
    *,
    clean: bool = False,
    strict: bool = False,
    bin_dir: Path | None = None,
    commit_of: Callable[[str], str | None] = installed_commit,
    timeout: float = STEP_TIMEOUT_SECONDS,
) -> Outcome:
    subject: dict[str, Any] = {"kind": "example", "path": EXAMPLE_NAME}
    command = "demo"
    try:
        out = confine_output(out_raw)
        directory = resolve_components(bin_dir, commit_of)
        _prepare_out(out, clean=clean)
    except PathRefused as exc:
        return Outcome(Result(command, INVALID, [OCS_PATHS], subject, messages=[str(exc)]))
    except ComponentProblem as exc:
        return Outcome(Result(command, INVALID, [OCS_VERSION], subject, messages=[str(exc)]))
    run = Run(out, directory, timeout)
    try:
        for name in TEMPLATES:
            (out / "inputs" / name).write_bytes(read_fixture(name))
        (out / "inputs" / "acp-run").mkdir()
        for name in ACP_RUN_FILES:
            (out / "inputs" / "acp-run" / name).write_bytes(read_acp_run_input(name))
        execute(run)
        result = _conclude(run, subject, strict)
    except StepFailed:
        result = _conclude(run, subject, strict)
    except FixtureIntegrityError as exc:
        result = Result(command, ERROR, [OCS_INTERNAL], subject, messages=[str(exc)])
    except StepRunError as exc:
        result = Result(
            command,
            ERROR,
            [OCS_INTERNAL],
            subject,
            messages=[str(exc)],
            extensions={"x-steps": [s.record() for s in run.steps]},
        )
    except (OSError, ValueError, render.RenderRefused) as exc:
        result = Result(
            command,
            ERROR,
            [OCS_INTERNAL],
            subject,
            messages=[f"demo could not complete ({type(exc).__name__})"],
            extensions={"x-steps": [s.record() for s in run.steps]},
        )
    with contextlib.suppress(OSError):
        (out / "demo-result.json").write_text(result.to_json(), encoding="utf-8")
        if result.exit_code != 0:
            write_diagnostics(result, out)
    return Outcome(result, out)


def _conclude(run: Run, subject: dict[str, Any], strict: bool) -> Result:
    steps = [s.record() for s in run.steps]
    facts = dict(run.facts)
    extensions: dict[str, Any] = {"x-steps": steps, "x-facts": facts}
    if run.control:
        extensions["x-control"] = run.control
    bad_steps = [s for s in run.steps if not s.ok]
    if bad_steps:
        first = bad_steps[0]
        return Result(
            "demo",
            FAIL,
            [OCS_DEMO_EXPECTATION],
            subject,
            messages=[
                f"step '{first.command}' exited {first.exit_code}, expected {first.expected}; see logs/",
            ],
            extensions=extensions,
        )
    missing = [name for name, held in facts.items() if not held]
    if missing:
        return Result(
            "demo",
            FAIL,
            [OCS_DEMO_EXPECTATION],
            subject,
            messages=[f"expected fact did not hold: {name}" for name in missing],
            extensions=extensions,
        )
    cp = COMPONENTS["acp"]
    source = {
        "component": cp.key.replace("_", "-"),
        "contract": CONTROL_CONTRACT,
        "contract_version": _component_version(cp.distribution),
    }
    codes = ", ".join(
        next(c["reason_codes"] for c in run.control if c["operation"] == "email_send")
    )
    evidenced = (
        f"the blocked email_send is evidenced in all four layers ({len(steps)} component "
        f"commands run offline, every exit code as expected); acp run held it: {codes}; "
        "view: evidence.html"
    )
    if strict:
        return Result(
            "demo",
            FAIL,
            [OCS_BLOCKING_FINDING],
            subject,
            verdict_source=source,
            messages=[
                evidenced,
                f"--strict: propagating the control plane's blocking decision on email_send (acp run exit 1: {codes})",
            ],
            extensions=extensions,
            strict=True,
        )
    return Result(
        "demo",
        PASS,
        [],
        subject,
        verdict_source=source,
        does_not_prove=list(DOES_NOT_PROVE),
        messages=[evidenced],
        extensions=extensions,
    )
