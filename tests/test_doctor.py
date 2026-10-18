"""`cognous-ocs doctor`: per-check results, the broken-scaffold acceptance case, and the read-only proof."""

from __future__ import annotations

import builtins
import io
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cognous_ocs import doctor, scaffold
from cognous_ocs.cli import main
from helpers import fake_bin, pinned


def scaffolded(ws: Path, name: str = "w") -> Path:
    assert main(["init", "workflow", "--out", name]) == 0
    return ws / name


def edit(path: Path, change: Any) -> None:
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc, indent=2) + "\n")


def checks(ws: Path, workflow: str | None, env: dict[str, str] | None = None) -> dict[str, Any]:
    out = doctor.run_doctor(workflow, bin_dir=fake_bin(ws.parent), commit_of=pinned, env=env or {})
    return {"outcome": out, "by_code": {c["code"]: c for c in out.result.extensions["x-checks"]}}


def status(result: dict[str, Any], code: str) -> str:
    return str(result["by_code"][code]["status"])


# ---- passing and skipping ---------------------------------------------------


def test_fresh_scaffold_is_ready(ws: Path) -> None:
    scaffolded(ws)
    result = checks(ws, "w")
    assert result["outcome"].result.exit_code == 0
    assert [status(result, c) for c in doctor.CHECK_CODES] == ["PASS"] * 6
    assert result["outcome"].result.does_not_prove


def test_without_a_workflow_only_version_runs_and_the_rest_skip(ws: Path) -> None:
    result = checks(ws, None)
    assert result["outcome"].result.exit_code == 0
    assert status(result, "OCS_VERSION") == "PASS"
    assert {status(result, c) for c in doctor.CHECK_CODES[1:]} == {"SKIP"}
    for record in result["by_code"].values():
        assert record["code"] and record["message"] and record["action"]


def test_every_check_has_a_code_message_and_one_action(ws: Path) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.POLICY_FILE, lambda d: d.update(blocked_tools=[]))
    for record in checks(ws, "w")["by_code"].values():
        assert set(record) == {"code", "status", "message", "action"}
        assert record["status"] in {"PASS", "FAIL", "SKIP"}
        assert record["action"]
        assert "\n" not in record["action"]


# ---- OCS_VERSION ------------------------------------------------------------


def test_version_fails_on_an_unpinned_or_missing_component(ws: Path) -> None:
    out = doctor.run_doctor(None, bin_dir=fake_bin(ws.parent), commit_of=lambda _d: "f" * 40)
    assert out.result.exit_code == 2 and out.result.reason_codes == ["OCS_VERSION"]
    empty = ws.parent / "empty"
    empty.mkdir()
    out = doctor.run_doctor(None, bin_dir=empty, commit_of=pinned)
    assert out.result.reason_codes == ["OCS_VERSION"]
    assert "pip install" in out.result.extensions["x-checks"][0]["action"]


# ---- OCS_MANIFEST -----------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda m: m["actions"][0].pop("action_type"), "no action_type"),
        (lambda m: m["actions"][1].pop("authority_required"), "no authority_required list"),
        (lambda m: m["actions"][1].update(authority_required=[]), "empty authority_required"),
        (lambda m: m["actions"][1].update(action_name=m["actions"][0]["action_name"]), "twice"),
        (lambda m: m["actions"][0].pop("action_name"), "no action_name"),
        (lambda m: m.update(actions=[]), "no actions"),
        (lambda m: m.update(actions=["x"]), "no action_name"),
        (lambda m: m.update(manifest_version="9.9"), "not a fully registered version"),
        (lambda m: m.pop("manifest_version"), "not a fully registered version"),
    ],
)
def test_manifest_failures_name_the_code(ws: Path, change: Any, expected: str) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.MANIFEST_FILE, change)
    result = checks(ws, "w")
    assert status(result, "OCS_MANIFEST") == "FAIL"
    assert expected in result["by_code"]["OCS_MANIFEST"]["message"]
    assert result["outcome"].result.exit_code == 2
    assert "OCS_MANIFEST" in result["outcome"].result.reason_codes


def test_manifest_missing_or_garbled_fails(ws: Path) -> None:
    scaffolded(ws)
    (ws / "w" / scaffold.MANIFEST_FILE).write_text("{")
    assert status(checks(ws, "w"), "OCS_MANIFEST") == "FAIL"
    (ws / "w" / scaffold.MANIFEST_FILE).unlink()
    result = checks(ws, "w")
    assert status(result, "OCS_MANIFEST") == "FAIL"
    assert status(result, "OCS_POLICY_BIND") == "SKIP"


# ---- OCS_POLICY_BIND --------------------------------------------------------


def test_policy_must_cover_every_manifest_action(ws: Path) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.POLICY_FILE, lambda p: p.update(blocked_tools=[]))
    result = checks(ws, "w")
    assert status(result, "OCS_POLICY_BIND") == "FAIL"
    assert "report_send" in result["by_code"]["OCS_POLICY_BIND"]["message"]


def test_policy_must_not_allow_and_block_the_same_action(ws: Path) -> None:
    scaffolded(ws)
    edit(
        ws / "w" / scaffold.POLICY_FILE,
        lambda p: p.update(allowed_tools=["notes_read", "report_send"]),
    )
    result = checks(ws, "w")
    assert (
        "allowed and blocked at once: report_send"
        in result["by_code"]["OCS_POLICY_BIND"]["message"]
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"allowed_tools": "notes_read", "blocked_tools": []},
        {},
        {"allowed_tools": [1], "blocked_tools": []},
    ],
)
def test_policy_shape_is_checked(ws: Path, bad: dict[str, Any]) -> None:
    scaffolded(ws)
    (ws / "w" / scaffold.POLICY_FILE).write_text(json.dumps(bad))
    assert status(checks(ws, "w"), "OCS_POLICY_BIND") == "FAIL"


def test_policy_missing_fails_and_unusable_manifest_skips(ws: Path) -> None:
    scaffolded(ws)
    (ws / "w" / scaffold.POLICY_FILE).unlink()
    assert status(checks(ws, "w"), "OCS_POLICY_BIND") == "FAIL"
    edit(ws / "w" / scaffold.MANIFEST_FILE, lambda m: m.update(actions=["x"]))
    (ws / "w" / scaffold.POLICY_FILE).write_text("{}")
    assert status(checks(ws, "w"), "OCS_POLICY_BIND") == "SKIP"


# ---- OCS_PATHS --------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["../escape.json", "/etc/passwd", "a\nb.json", "", 7, "sub/../../x"]
)
def test_workflow_references_must_stay_inside_the_directory(ws: Path, value: object) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.update(run_record=value))
    result = checks(ws, "w")
    assert status(result, "OCS_PATHS") == "FAIL"
    assert "OCS_PATHS" in result["outcome"].result.reason_codes


def test_symlinked_reference_is_refused(ws: Path) -> None:
    scaffolded(ws)
    (ws / "elsewhere.json").write_text("{}")
    target = ws / "w" / "linked.json"
    target.symlink_to(ws / "elsewhere.json")
    edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.update(manifest="linked.json"))
    result = checks(ws, "w")
    assert (
        status(result, "OCS_PATHS") == "FAIL"
        and "symlink" in result["by_code"]["OCS_PATHS"]["message"]
    )
    assert status(result, "OCS_MANIFEST") == "SKIP"


def test_missing_reference_key_and_missing_workflow_file(ws: Path) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.pop("policy"))
    assert "does not name 'policy'" in checks(ws, "w")["by_code"]["OCS_PATHS"]["message"]
    (ws / "w" / scaffold.WORKFLOW_FILE).unlink()
    result = checks(ws, "w")
    assert status(result, "OCS_PATHS") == "FAIL"
    assert status(result, "OCS_SIGNING") == "SKIP" and status(result, "OCS_REDACTION") == "SKIP"


def test_workflow_path_must_be_a_directory_without_control_characters(ws: Path) -> None:
    (ws / "afile").write_text("x")
    for raw in ("afile", "nope", "a\nb"):
        result = checks(ws, raw)
        assert status(result, "OCS_PATHS") == "FAIL"
        assert result["outcome"].result.exit_code == 2


# ---- OCS_SIGNING and OCS_REDACTION ------------------------------------------


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {},
        {"mode": "none"},
        {"mode": 3},
        "demo-only",
        {"mode": "secret-env"},
        {"mode": "secret-env", "env": "bad name"},
    ],
)
def test_signing_must_be_configured_or_marked_demo_only(ws: Path, cfg: object) -> None:
    scaffolded(ws)

    def change(w: dict[str, Any]) -> None:
        w.pop("replay_signing")
        if cfg is not None:
            w["replay_signing"] = cfg

    edit(ws / "w" / scaffold.WORKFLOW_FILE, change)
    assert status(checks(ws, "w"), "OCS_SIGNING") == "FAIL"


def test_secret_env_signing_needs_the_variable_and_never_prints_it(ws: Path) -> None:
    scaffolded(ws)
    edit(
        ws / "w" / scaffold.WORKFLOW_FILE,
        lambda w: w.update(replay_signing={"mode": "secret-env", "env": "MY_SIGNING_KEY"}),
    )
    assert status(checks(ws, "w", {}), "OCS_SIGNING") == "FAIL"
    assert status(checks(ws, "w", {"MY_SIGNING_KEY": ""}), "OCS_SIGNING") == "FAIL"
    result = checks(ws, "w", {"MY_SIGNING_KEY": "s3cr3t-value"})
    assert status(result, "OCS_SIGNING") == "PASS"
    assert "s3cr3t-value" not in json.dumps(result["outcome"].result.to_dict())


@pytest.mark.parametrize("profile", [None, "", "nope", 5, ["targets"]])
def test_redaction_profile_must_be_named_and_loadable(ws: Path, profile: object) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.update(redaction={"profile": profile}))
    assert status(checks(ws, "w"), "OCS_REDACTION") == "FAIL"


def test_redaction_section_missing_fails(ws: Path) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.pop("redaction"))
    assert status(checks(ws, "w"), "OCS_REDACTION") == "FAIL"


@pytest.mark.parametrize("name", list(scaffold.REDACTION_PROFILES))
def test_every_known_profile_passes(ws: Path, name: str) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.update(redaction={"profile": name}))
    assert status(checks(ws, "w"), "OCS_REDACTION") == "PASS"


# ---- acceptance: a broken scaffold names the failing code --------------------


def test_broken_scaffold_names_the_failing_code_through_the_cli(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scaffolded(ws)
    edit(ws / "w" / scaffold.POLICY_FILE, lambda p: p.update(blocked_tools=[]))
    capsys.readouterr()
    assert main(["doctor", "--workflow", "w"]) == 2
    text = capsys.readouterr().out
    assert "FAIL OCS_POLICY_BIND" in text and "action:" in text
    assert "reason codes: OCS_POLICY_BIND" in text
    assert main(["--format", "json", "doctor", "--workflow", "w"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["outcome"] == "invalid" and body["reason_codes"] == ["OCS_POLICY_BIND"]
    failed = [c for c in body["extensions"]["x-checks"] if c["status"] == "FAIL"]
    assert [c["code"] for c in failed] == ["OCS_POLICY_BIND"]


# ---- read-only, enforced ----------------------------------------------------------


def tree(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    out: dict[str, tuple[int, int, bytes | None]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            st = p.lstat()
            out[str(p.relative_to(root))] = (
                st.st_mtime_ns,
                st.st_size,
                p.read_bytes() if p.is_file() and not p.is_symlink() else None,
            )
    return out


def forbid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any write, process spawn or socket use raise while doctor runs."""

    def no(*_a: object, **_k: object) -> None:
        raise AssertionError("doctor must be read-only, offline and must not spawn processes")

    real_open = io.open

    def guarded_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            no()
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    for name in (
        "mkdir",
        "makedirs",
        "rename",
        "replace",
        "remove",
        "unlink",
        "rmdir",
        "symlink",
        "link",
        "utime",
        "chmod",
        "truncate",
    ):
        monkeypatch.setattr(os, name, no)
    monkeypatch.setattr(subprocess, "run", no)
    monkeypatch.setattr(subprocess, "Popen", no)
    monkeypatch.setattr(socket, "socket", no)
    monkeypatch.setattr(socket, "create_connection", no)
    monkeypatch.setattr(socket, "getaddrinfo", no)


@pytest.mark.parametrize("broken", [False, True])
def test_doctor_is_strictly_read_only(
    ws: Path, monkeypatch: pytest.MonkeyPatch, broken: bool
) -> None:
    scaffolded(ws)
    if broken:
        edit(ws / "w" / scaffold.POLICY_FILE, lambda p: p.update(blocked_tools=[]))
        edit(ws / "w" / scaffold.WORKFLOW_FILE, lambda w: w.pop("replay_signing"))
    # A declared diagnostics directory must not be created by a read-only command either.
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "diag")
    before = tree(ws)
    forbid(monkeypatch)
    code = main(["doctor", "--workflow", "w"])
    assert code == 2 or not broken
    after = tree(ws)
    monkeypatch.undo()
    assert after == before, "directory tree, mtimes, sizes and bytes are identical"
    assert not (ws / "diag").exists()


def test_doctor_without_a_workflow_is_also_read_only(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tree(ws)
    forbid(monkeypatch)
    main(["doctor"])
    after = tree(ws)
    monkeypatch.undo()
    assert after == before


def test_the_guards_really_block_writes_spawns_and_sockets(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid(monkeypatch)
    with pytest.raises(AssertionError):
        (ws / "x").write_text("x")
    with pytest.raises(AssertionError):
        Path("made").mkdir()
    with pytest.raises(AssertionError):
        subprocess.run(["true"], check=False)
    with pytest.raises(AssertionError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    monkeypatch.undo()
    assert not (ws / "x").exists()


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_sockets_are_disabled_for_the_whole_test_run() -> None:
    with pytest.raises(Exception, match=r"(?i)socket"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
