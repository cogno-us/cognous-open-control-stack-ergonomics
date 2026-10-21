from __future__ import annotations

import json
from pathlib import Path

import pytest

from cognous_ocs import CONTRACT_VERSION, __version__
from cognous_ocs.cli import main


def test_version_prints_cli_contract_and_example(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    out = capsys.readouterr().out
    assert f"cognous-ocs {__version__}" in out
    assert f"cli-contract {CONTRACT_VERSION}" in out
    assert "bundled-example customer-service 0.2.0" in out
    for command in ("aam", "acp", "arb", "agep"):
        assert f"bundled-component {command} " in out


def test_no_command_is_invalid_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "no command" in capsys.readouterr().out


def test_unknown_command_json_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--format", "json", "frobnicate"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["envelope_version"] == "1"
    assert body["outcome"] == "invalid"
    assert body["reason_codes"] == ["OCS_INPUT_INVALID"]


def test_json_stdout_is_exactly_one_object(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--format=json"])
    out = capsys.readouterr().out
    assert out.count("\n") > 1
    assert json.loads(out)["exit_code"] == 2


def test_diagnostics_written_when_env_names_a_confined_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "diag")
    assert main([]) == 2
    body = json.loads((work / "diag" / "ci-diagnostics.json").read_text())
    assert body["exit_code"] == 2
    assert body["outcome"] == "invalid"


def test_diagnostics_dir_outside_roots_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "/etc/ocs-diag")
    assert main([]) == 2
    assert not Path("/etc/ocs-diag").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["-h"],
        ["validate", "--help"],
        ["--format", "json", "--help"],
        ["--format", "json", "validate", "--help"],
        ["validate", "--format=json", "-h"],
        ["demo", "--help"],
        ["ci", "--help"],
    ],
)
def test_help_exits_zero_with_help_text_only(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: cognous-ocs")
    assert "envelope_version" not in out  # help text only, never help followed by an envelope


def test_help_is_never_reported_as_a_validation_pass(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "diag")
    assert main(["--format", "json", "validate", "--help"]) == 0
    out = capsys.readouterr().out
    assert '"outcome"' not in out
    assert not (tmp_path / "diag").exists()  # no result, so no diagnostics file either


def test_usage_errors_stay_invalid_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--format", "json", "validate", "--bogus"]) == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "invalid"
