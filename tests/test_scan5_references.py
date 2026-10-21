"""Scan 5 C-T1-3: a workflow artifact named in workflow.json but absent must fail, never pass."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cognous_ocs import ci, doctor, fixtures_data, scaffold, validate
from cognous_ocs.cli import Outcome, main
from cognous_ocs.envelope import Result
from helpers import fake_bin, pinned

SEEN: list[str] = []
SRC = {"component": "x", "contract": "run-record", "contract_version": "1"}


@pytest.fixture
def wf(ws: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    assert main(["init", "workflow", "--out", "w"]) == 0
    (ws / "pack.json").write_bytes(fixtures_data.read_fixture("evidence-pack.json"))
    SEEN.clear()

    def passing(path: str, kind: str, **_: Any) -> Outcome:
        SEEN.append(kind)
        return Outcome(
            Result(
                "validate",
                "pass",
                [],
                {"kind": kind, "path": path},
                verdict_source=SRC,
                does_not_prove=["x"],
            )
        )

    def version_ok(bin_dir: Path | None, commit_of: Any) -> doctor.Check:
        return doctor.Check("OCS_VERSION", doctor.OK, "stubbed")

    monkeypatch.setattr(validate, "run_validate", passing)
    monkeypatch.setattr(doctor, "check_version", version_ok)
    return ws / "w"


def go() -> Outcome:
    return ci.run_ci("ciout", workflow="w", evidence_pack="pack.json")


def checks_run(out: Outcome) -> list[str]:
    return [s["check"] for s in out.result.extensions["x-steps"]]


def test_present_files_pass(wf: Path) -> None:
    out = go()
    assert out.result.outcome == "pass"
    assert SEEN == ["manifest", "run-record", "evidence-pack"]


@pytest.mark.parametrize("name", [scaffold.RUN_RECORD_FILE, scaffold.MANIFEST_FILE])
def test_missing_referenced_file_fails_ci(wf: Path, name: str) -> None:
    (wf / name).unlink()
    out = go()
    assert out.result.outcome == "invalid"
    assert out.result.exit_code == 2
    assert "OCS_PATHS" in out.result.reason_codes
    assert any("(reference)" in c for c in checks_run(out))


def test_directory_instead_of_file_fails_ci(wf: Path) -> None:
    (wf / scaffold.RUN_RECORD_FILE).unlink()
    (wf / scaffold.RUN_RECORD_FILE).mkdir()
    out = go()
    assert out.result.outcome == "invalid"
    assert "run-record" not in SEEN


def test_symlink_escape_fails_ci(wf: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    (wf / scaffold.RUN_RECORD_FILE).unlink()
    os.symlink(target, wf / scaffold.RUN_RECORD_FILE)
    out = go()
    assert out.result.outcome == "invalid"
    assert "run-record" not in SEEN


def test_summary_file_is_not_pass_when_a_reference_is_missing(wf: Path, ws: Path) -> None:
    (wf / scaffold.RUN_RECORD_FILE).unlink()
    go()
    summary = json.loads((ws / "ciout" / ci.SUMMARY_JSON).read_text())
    assert summary["outcome"] != "pass"


def doctor_checks(ws: Path) -> dict[str, Any]:
    out = doctor.run_doctor("w", bin_dir=fake_bin(ws.parent), commit_of=pinned, env={})
    return {c["code"]: c for c in out.result.extensions["x-checks"]}


def test_doctor_paths_pass_for_a_fresh_scaffold(wf: Path, ws: Path) -> None:
    assert doctor_checks(ws)["OCS_PATHS"]["status"] == "PASS"


@pytest.mark.parametrize("name", [scaffold.RUN_RECORD_FILE, scaffold.POLICY_FILE])
def test_doctor_paths_fail_when_a_referenced_file_is_missing(wf: Path, ws: Path, name: str) -> None:
    (wf / name).unlink()
    check = doctor_checks(ws)["OCS_PATHS"]
    assert check["status"] == "FAIL"
    assert "plain file" in check["message"]


def test_doctor_paths_fail_for_a_directory_and_a_symlink(wf: Path, ws: Path) -> None:
    (wf / scaffold.RUN_RECORD_FILE).unlink()
    (wf / scaffold.RUN_RECORD_FILE).mkdir()
    assert doctor_checks(ws)["OCS_PATHS"]["status"] == "FAIL"
    (wf / scaffold.RUN_RECORD_FILE).rmdir()
    os.symlink(wf / scaffold.MANIFEST_FILE, wf / scaffold.RUN_RECORD_FILE)
    assert doctor_checks(ws)["OCS_PATHS"]["status"] == "FAIL"
