"""Demo orchestration: fake component commands for units, the real pinned ones for integration."""

from __future__ import annotations

import importlib.metadata as md
import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cognous_ocs import demo
from cognous_ocs import fixtures_data as fx
from cognous_ocs.cli import main
from cognous_ocs.envelope import FAIL, INVALID
from cognous_ocs.lock import COMPONENTS


def fake_bin(root: Path, bodies: dict[str, str] | None = None) -> Path:
    directory = root / "fakebin"
    directory.mkdir(exist_ok=True)
    for comp in COMPONENTS.values():
        script = directory / comp.command
        script.write_text((bodies or {}).get(comp.command, "#!/bin/sh\nexit 0\n"))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return directory


def pinned(distribution: str) -> str | None:
    return {c.distribution: c.sha for c in COMPONENTS.values()}.get(distribution)


# ---- component resolution -------------------------------------------------


def test_resolve_accepts_pinned_components(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    assert demo.resolve_components(directory, pinned) == directory


def test_resolve_refuses_missing_command(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    (directory / "arb").unlink()
    with pytest.raises(demo.ComponentProblem, match=r"arb .* is not installed"):
        demo.resolve_components(directory, pinned)


def test_resolve_refuses_wrong_or_unknown_commit(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)

    def wrong(dist: str) -> str | None:
        return None if dist == "agent-control-plane" else "f" * 40

    with pytest.raises(demo.ComponentProblem) as info:
        demo.resolve_components(directory, wrong)
    text = str(info.value)
    assert "unknown source" in text and "expected" in text and "cognous-ocs[stack]" in text


def test_installed_commit_reads_pep610(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dist:
        def __init__(self, raw: str | None) -> None:
            self.raw = raw

        def read_text(self, name: str) -> str | None:
            assert name == "direct_url.json"
            return self.raw

    cases: list[tuple[str | None, str | None]] = [
        (json.dumps({"vcs_info": {"commit_id": "abc"}}), "abc"),
        (None, None),
        ("not json", None),
        (json.dumps({"url": "file:///x"}), None),
        (json.dumps({"vcs_info": {"commit_id": 5}}), None),
    ]
    for raw, expected in cases:
        monkeypatch.setattr(md, "distribution", lambda _n, raw=raw: Dist(raw))
        assert demo.installed_commit("anything") == expected

    def missing(_n: str) -> Dist:
        raise md.PackageNotFoundError

    monkeypatch.setattr(md, "distribution", missing)
    assert demo.installed_commit("anything") is None


# ---- output directory handling ---------------------------------------------


def test_missing_components_is_version_error_exit_2(
    ws: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_a: object, **_k: object) -> Path:
        raise demo.ComponentProblem("arb (agent-replay-bundle) is not installed")

    monkeypatch.setattr(demo, "resolve_components", refuse)
    code = main(["--format", "json", "demo", "customer-service", "--out", "d"])
    body = json.loads(capsys.readouterr().out)
    assert code == 2 and body["reason_codes"] == ["OCS_VERSION"] and body["outcome"] == "invalid"
    assert not (ws / "d").exists()


def test_non_empty_dir_needs_clean(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    (ws / "d").mkdir()
    (ws / "d" / "mine.txt").write_text("keep me")
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    assert outcome.result.outcome == INVALID and outcome.result.reason_codes == ["OCS_PATHS"]
    assert (ws / "d" / "mine.txt").read_text() == "keep me"


def test_clean_refuses_a_directory_it_did_not_create(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    (ws / "d").mkdir()
    (ws / "d" / "mine.txt").write_text("keep me")
    outcome = demo.run_demo("d", clean=True, bin_dir=directory, commit_of=pinned)
    assert outcome.result.reason_codes == ["OCS_PATHS"]
    assert "marker" in outcome.result.messages[0]
    assert (ws / "d" / "mine.txt").read_text() == "keep me"


def test_out_that_is_a_file_is_refused(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    (ws / "d").write_text("a file")
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    assert outcome.result.reason_codes == ["OCS_PATHS"]


def test_out_outside_workspace_is_refused(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    outcome = demo.run_demo("/etc/ocs-demo", bin_dir=directory, commit_of=pinned)
    assert outcome.result.reason_codes == ["OCS_PATHS"]
    assert not Path("/etc/ocs-demo").exists()


def test_clean_replaces_a_previous_demo_and_leaves_no_stale_files(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    demo.run_demo("d", bin_dir=directory, commit_of=pinned)  # fake components: run fails midway
    (ws / "d" / "stale.txt").write_text("old")
    (ws / "d" / "link").symlink_to(ws)
    outcome = demo.run_demo("d", clean=True, bin_dir=directory, commit_of=pinned)
    assert outcome.result.reason_codes != ["OCS_PATHS"]
    assert not (ws / "d" / "stale.txt").exists()
    assert not (ws / "d" / "link").exists() and ws.exists()


# ---- step handling (fake components) ----------------------------------------


def test_unexpected_step_exit_fails_with_demo_expectation(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path, {"aam": "#!/bin/sh\nexit 3\n"})
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    result = outcome.result
    assert result.outcome == FAIL and result.exit_code == 1
    assert result.reason_codes == ["OCS_DEMO_EXPECTATION"]
    assert "exited 3, expected 0" in result.messages[0]
    steps = result.extensions["x-steps"]
    assert len(steps) == 1 and steps[0]["ok"] is False  # fails fast
    diag = json.loads((ws / "d" / "ci-diagnostics.json").read_text())
    assert diag["exit_code"] == 1 and "messages" not in diag
    assert json.loads((ws / "d" / "demo-result.json").read_text())["outcome"] == "fail"
    assert (ws / "d" / "logs" / "01-aam.txt").is_file()


def test_control_step_passes_a_fixed_now_and_expects_exit_1_for_the_send(
    ws: Path, tmp_path: Path
) -> None:
    """A control plane that applies the send (exit 0 everywhere) is a demo failure."""
    argv_log = tmp_path / "argv.txt"
    body = f'#!/bin/sh\necho "$@" >> {argv_log}\nexit 0\n'
    directory = fake_bin(tmp_path, {"acp": body})
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    result = outcome.result
    assert result.reason_codes == ["OCS_DEMO_EXPECTATION"] and result.exit_code == 1
    assert "exited 0, expected 1" in result.messages[0] and "email-send" in result.messages[0]
    calls = argv_log.read_text().splitlines()
    runs = [c for c in calls if c.startswith("run ")]
    assert len(runs) == 3
    assert all("--now 2026-10-05T19:00:00Z" in c and "--format json" in c for c in runs)
    assert [c.split()[0] for c in calls] == ["run", "validate-run"] * 2 + ["run"]


def test_step_timeout_is_an_internal_error(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path, {"aam": "#!/bin/sh\nexec /bin/sleep 5\n"})
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned, timeout=0.2)
    assert outcome.result.exit_code == 4
    assert outcome.result.reason_codes == ["OCS_INTERNAL"]


def test_step_that_cannot_exec_is_an_internal_error(ws: Path, tmp_path: Path) -> None:
    directory = fake_bin(tmp_path, {"aam": "no shebang and not executable text\n"})
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    assert outcome.result.exit_code == 4


def test_tampered_fixture_is_an_internal_error(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = fake_bin(tmp_path)

    def bad(name: str) -> bytes:
        raise fx.FixtureIntegrityError(f"fixture {name} does not match its recorded hash")

    monkeypatch.setattr(demo, "read_fixture", bad)
    outcome = demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    assert outcome.result.exit_code == 4 and "recorded hash" in outcome.result.messages[0]


def test_log_text_is_control_stripped(ws: Path, tmp_path: Path) -> None:
    body = "#!/bin/sh\nprintf 'ok\\033[31m red\\001\\nnext\\n'\nexit 3\n"
    directory = fake_bin(tmp_path, {"aam": body})
    demo.run_demo("d", bin_dir=directory, commit_of=pinned)
    log = (ws / "d" / "logs" / "01-aam.txt").read_text()
    assert "\x1b" not in log and "\x01" not in log and "next" in log


# ---- integration: the real pinned components --------------------------------


@pytest.mark.integration
def test_demo_end_to_end_exit_0(stack: None, ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--format", "json", "demo", "customer-service", "--out", "demo-out"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["outcome"] == "pass" and body["exit_code"] == 0
    assert body["envelope_version"] == "1"
    assert body["verdict_source"]["component"] == "control-plane"
    assert body["does_not_prove"]
    assert all(step["ok"] for step in body["extensions"]["x-steps"])
    assert all(body["extensions"]["x-facts"].values())
    assert "ocs-demo-only" not in json.dumps(body)
    layers = {step["layer"] for step in body["extensions"]["x-steps"]}
    assert layers == {"declare", "control", "replay", "evidence"}
    page = (ws / "demo-out" / "evidence.html").read_text(encoding="utf-8")
    assert 'chip blocked">blocked' in page and "<code>email_send</code>" in page
    assert "grant_scope_mismatch" in page
    assert (ws / "demo-out" / "demo-result.json").is_file()
    # The control step is the real `acp run`: the send is its exit 1, reasons passed through.
    control = {c["operation"]: c for c in body["extensions"]["x-control"]}
    assert [control[n]["exit"] for n in ("crm_read", "notes_search", "email_send")] == [0, 0, 1]
    assert control["email_send"]["reason_codes"] == ["grant_scope_mismatch"]
    assert control["email_send"]["destination_effects"] == 0
    acp_steps = [s for s in body["extensions"]["x-steps"] if s["command"].startswith("acp run")]
    assert [s["exit"] for s in acp_steps] == [0, 0, 1]
    assert all("--now 2026-10-05T19:00:00Z" in s["command"] for s in acp_steps)
    assert (
        sum(s["command"].startswith("acp validate-run") for s in body["extensions"]["x-steps"]) == 3
    )
    unproven = " ".join(body["does_not_prove"])
    assert "did not run" not in unproven and "synthetic" in unproven
    assert "local file destination" in unproven and "projection" in unproven
    send_out = ws / "demo-out" / "control" / "email_send" / "out"
    assert (send_out / "bounded-run-record.json").is_file()
    row = json.loads((send_out / "run-record.json").read_text())["blocked_actions"][0]
    assert row["reason"] == "grant_scope_mismatch"
    assert not (ws / "demo-out" / "ci-diagnostics.json").exists()


@pytest.mark.integration
def test_demo_strict_propagates_the_blocking_result(
    stack: None, ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--format", "json", "--strict", "demo", "customer-service", "--out", "d"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["outcome"] == "fail" and body["reason_codes"] == ["OCS_BLOCKING_FINDING"]
    assert body["verdict_source"]["component"] == "control-plane"
    assert (ws / "d" / "ci-diagnostics.json").is_file()


@pytest.mark.integration
def test_demo_rerun_needs_clean_then_replaces(stack: None, ws: Path) -> None:
    assert main(["demo", "customer-service", "--out", "d"]) == 0
    assert main(["demo", "customer-service", "--out", "d"]) == 2
    assert main(["demo", "customer-service", "--out", "d", "--clean"]) == 0


def _altered_acp_input(
    monkeypatch: pytest.MonkeyPatch, name: str, change: Callable[[dict[str, Any]], None]
) -> None:
    real: Callable[[str], bytes] = fx.read_acp_run_input

    def altered(requested: str) -> bytes:
        data = real(requested)
        if requested != name:
            return data
        doc = json.loads(data)
        change(doc)
        return json.dumps(doc).encode()

    monkeypatch.setattr(demo, "read_acp_run_input", altered)


@pytest.mark.integration
def test_demo_fails_when_the_control_plane_lets_the_send_through(
    stack: None, ws: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Make the grant cover outbound.send: acp run applies it (exit 0), the demo refuses."""

    def cover_send(doc: dict[str, Any]) -> None:
        permissions = doc["grant"]["permissions"]
        send = next(p for p in doc["requirement"]["permissions"] if p["action"] == "email_send")
        permissions.append(send)

    _altered_acp_input(monkeypatch, "authority-context.json", cover_send)
    code = main(["--format", "json", "demo", "customer-service", "--out", "d"])
    body = json.loads(capsys.readouterr().out)
    assert code == 1 and body["reason_codes"] == ["OCS_DEMO_EXPECTATION"]
    assert "acp run" in body["messages"][0] and "exited 0, expected 1" in body["messages"][0]


@pytest.mark.integration
def test_demo_fails_when_the_hold_has_other_reason_codes(
    stack: None, ws: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Still held, but not for the uncovered permission: the expectation does not hold."""

    def over_limit(doc: dict[str, Any]) -> None:
        doc["amount"] = 5.0

    _altered_acp_input(monkeypatch, "proposal-email-send.json", over_limit)
    code = main(["--format", "json", "demo", "customer-service", "--out", "d"])
    body = json.loads(capsys.readouterr().out)
    assert code == 1 and body["reason_codes"] == ["OCS_DEMO_EXPECTATION"]
    assert any("reason codes are passed through unchanged" in m for m in body["messages"])
    control = {c["operation"]: c for c in body["extensions"]["x-control"]}
    assert "amount_exceeded" in control["email_send"]["reason_codes"]  # the component's, unchanged


@pytest.mark.integration
def test_real_components_reject_the_negative_controls(stack: None, ws: Path) -> None:
    assert main(["demo", "customer-service", "--out", "d"]) == 0
    result = json.loads((ws / "d" / "demo-result.json").read_text())
    negatives = [s for s in result["extensions"]["x-steps"] if s["expected_exit"] == 1]
    # the held send (acp run exit 1) and the two replay negative controls
    assert [s["command"].split()[0:2] for s in negatives] == [
        ["acp", "run"],
        ["arb", "verify"],
        ["arb", "verify"],
    ]
    assert all(s["exit"] == 1 for s in negatives)
