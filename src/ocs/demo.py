"""`ocs demo customer-service`: the bundled example, run end to end, offline.

Every layer is checked by the real component command in a subprocess: declare (aam), control
(acp), replay (arb) and evidence (agep). `ocs` never imports component code. It asserts each
exit code, reads the artifacts the components accepted, and succeeds only if the blocked email
send is evidenced in every layer. It decides nothing itself.
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

from ocs import EXAMPLE_NAME, render
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
from ocs.fixtures_data import FIXTURE_FILES, FixtureIntegrityError, read_fixture
from ocs.lock import COMPONENTS
from ocs.paths import PathRefused, confine_output

MARKER = ".ocs-demo-output"
# Public, intentionally not secret: the replay signature in this demo proves nothing about a producer.
DEMO_SECRET = "ocs-demo-only-not-a-secret"  # noqa: S105
WRONG_SECRET = "wrong-key"  # noqa: S105
KEYS = {DEMO_SECRET, WRONG_SECRET}
STEP_TIMEOUT_SECONDS = 60.0
LOG_LIMIT = 20_000

DOES_NOT_PROVE = [
    "that the control plane ran: the run record is a bundled synthetic fixture checked by "
    "acp validate-run, not produced by this demo",
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
            "; ".join(problems) + ". Install with: pip install 'ocs[stack]' (from this repository)"
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
    for sub in ("inputs", "outputs", "logs"):
        (out / sub).mkdir()


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _by(items: Any, key: str) -> dict[str, dict[str, Any]]:
    return {str(i.get(key)): i for i in items if isinstance(i, dict)}


def check_facts(run: Run) -> None:
    """Read the accepted artifacts and record which expected facts hold."""
    inp = run.out / "inputs"
    outs = run.out / "outputs"
    facts = run.facts
    manifest = _load(inp / "manifest.json")
    record = _load(inp / "run-record.json")
    bundle = _load(inp / "replay-bundle.json")
    pack = _load(inp / "evidence-pack.json")

    declared = _by(manifest.get("actions", []), "action_name").get("email_send", {})
    facts["declare: email_send is declared as an external send needing authority"] = declared.get(
        "action_type"
    ) == "external_send" and any(a.get("required") for a in declared.get("authority_required", []))

    actions = _by(record.get("actions", []), "tool_name")
    decisions = _by(record.get("decisions", []), "action_id")
    send = actions.get("email_send", {})
    send_decision = decisions.get(str(send.get("action_id")), {})
    blocked_ids = {str(b.get("action_id")): b for b in record.get("blocked_actions", [])}
    allowed_tools = {
        t
        for t, a in actions.items()
        if decisions.get(str(a.get("action_id")), {}).get("result") == "allow"
    }
    facts["control: crm_read and notes_search are allowed"] = {
        "crm_read",
        "notes_search",
    } <= allowed_tools
    facts["control: email_send is blocked, never allowed"] = (
        send_decision.get("result") == "block"
        and "email_send" not in allowed_tools
        and str(send.get("action_id")) in blocked_ids
    )
    facts["control: the block reason names the missing authority"] = (
        "authority" in str(send_decision.get("reason", "")).lower()
    )
    facts["control: reliance is recorded for both reads"] = (
        len(record.get("reliance_records", [])) >= 2
    )

    replay_blocked = _by(bundle.get("blocked_actions", []), "action_id")
    send_id = str(send.get("action_id"))
    facts["replay: the replay bundle carries the same blocked send as the run record"] = (
        send_id in replay_blocked
        and replay_blocked[send_id].get("blocked_id")
        == blocked_ids.get(send_id, {}).get("blocked_id")
        and bundle.get("run_id") == record.get("run_id")
    )
    redacted_text = (outs / "redacted-replay-bundle.json").read_text(encoding="utf-8")
    original_target = str(send.get("target"))
    facts["replay: target redaction removed the recipient from the redacted bundle"] = (
        bool(original_target) and original_target not in redacted_text
    )
    signed = _load(outs / "signed-replay-bundle.json")
    facts["replay: the signed bundle carries a signature"] = bool(
        signed.get("signature_metadata", {}).get("signature")
    )

    pack_blocked = _by(pack.get("blocked_actions", []), "action_name").get("email_send", {})
    facts["evidence: the pack records the email_send block with a reason"] = int(
        pack_blocked.get("count", 0)
    ) >= 1 and bool(pack_blocked.get("reason_summary"))
    facts["evidence: the pack lists the replay bundle"] = bundle.get("bundle_id") in {
        b.get("bundle_id") for b in pack.get("replay_bundles", [])
    }
    page = (run.out / "evidence.html").read_text(encoding="utf-8")
    facts["evidence: the HTML view shows the blocked email_send with its reason"] = (
        'chip blocked">blocked' in page
        and "<code>email_send</code>" in page
        and "outbound-send authority is missing" in page
    )


def execute(run: Run) -> None:
    """Run every step. Raises StepRunError if a step cannot be executed at all."""
    inp, outs = run.out / "inputs", run.out / "outputs"
    manifest, record = str(inp / "manifest.json"), str(inp / "run-record.json")
    bundle, pack = str(inp / "replay-bundle.json"), str(inp / "evidence-pack.json")
    signed = str(outs / "signed-replay-bundle.json")
    redacted = str(outs / "redacted-replay-bundle.json")
    tampered = outs / "tampered-signed-replay-bundle.json"

    run.exec("declare", "aam", ["validate", manifest])
    run.exec("declare", "aam", ["list-actions", manifest])
    run.exec("control", "acp", ["validate-run", record])
    run.exec("replay", "arb", ["validate", bundle])
    run.exec("replay", "arb", ["sign", bundle, "--secret", DEMO_SECRET, "--out", signed])
    run.exec("replay", "arb", ["verify", signed, "--secret", DEMO_SECRET])
    run.exec("replay", "arb", ["redact", bundle, "--out", redacted, "--targets"])
    run.exec("replay", "arb", ["validate", redacted])
    # Negative controls: the component must reject a wrong key and an edited bundle.
    run.exec("replay", "arb", ["verify", signed, "--secret", WRONG_SECRET], expected=1)
    doc = _load(Path(signed))
    doc["replay_bundle"]["final_output"] = "edited after signing"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    run.exec("replay", "arb", ["verify", str(tampered), "--secret", DEMO_SECRET], expected=1)
    run.exec("evidence", "agep", ["validate", pack])
    run.exec("evidence", "agep", ["summarize", pack])
    run.exec("evidence", "agep", ["render", pack, "--out", str(outs / "evidence-pack.md")])
    loaded = render.load_pack(inp / "evidence-pack.json")
    (run.out / "evidence.html").write_bytes(render.render_html(loaded.data).encode("utf-8"))
    check_facts(run)


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
        for name in FIXTURE_FILES:
            (out / "inputs" / name).write_bytes(read_fixture(name))
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
    extensions = {"x-steps": steps, "x-facts": facts}
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
        "contract": cp.contract,
        "contract_version": _component_version(cp.distribution),
    }
    evidenced = (
        f"the blocked email_send is evidenced in all four layers ({len(steps)} component "
        "commands run offline, every exit code as expected); view: evidence.html"
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
                "--strict: propagating the control plane's blocking decision on email_send",
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
