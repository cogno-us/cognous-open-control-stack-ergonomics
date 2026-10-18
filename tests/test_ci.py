"""`ocs ci`: fail-closed profile, version binding, confinement, gating, reports, diagnostics."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ocs import __version__, ci, fixtures_data, validate
from ocs.cli import Outcome, main
from ocs.doctor import FAILED, OK, Check
from ocs.envelope import Result

SRC = {"component": "action-manifest", "contract": "action-manifest", "contract_version": "1.0.0"}
SRC_PACK = {
    "component": "governance-evidence-pack",
    "contract": "evidence-pack",
    "contract_version": "1",
}


def run(argv: list[str]) -> tuple[int, dict[str, Any]]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["ci", *argv, "--format", "json"])
    return code, json.loads(buf.getvalue())


@pytest.fixture
def work(ws: Path) -> Path:
    from ocs import scaffold

    wf = ws / "wf"
    wf.mkdir()
    for name, content in scaffold.managed_bytes().items():
        (wf / name).write_bytes(content)
    (ws / "pack.json").write_bytes(fixtures_data.read_fixture("evidence-pack.json"))
    (ws / "manifest.json").write_bytes(fixtures_data.read_fixture("manifest.json"))
    return ws


class Fake:
    """Replace the component-backed checks with scripted outcomes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, str]) -> None:
        self.calls: list[str] = []
        self.outcomes = outcomes
        monkeypatch.setattr(validate, "run_validate", self.validate)
        monkeypatch.setattr(ci, "run_checks", self.doctor)

    def validate(self, path: str, kind: str, **_: Any) -> Outcome:
        self.calls.append(kind)
        outcome = self.outcomes.get(kind, "pass")
        src = SRC_PACK if kind == "evidence-pack" else SRC
        code = {"pass": 0, "invalid": 2, "error": 4, "fail": 1, "review": 0}[outcome]
        codes = [] if outcome == "pass" else ["OCS_INPUT_INVALID"]
        return Outcome(
            Result(
                "validate",
                outcome,
                codes,
                {"kind": kind, "path": path, "exit": code},
                verdict_source=src,
                does_not_prove=["x"],
                messages=["m"],
            )
        )

    def doctor(self, workflow: str | None, **_: Any) -> list[Check]:
        self.calls.append("doctor")
        bad = self.outcomes.get("doctor") == "invalid"
        return [Check("OCS_PATHS", FAILED if bad else OK, "msg", "act")]


# profile, version, paths


def test_refuses_without_evidence_or_non_assurance_and_writes_diagnostics(work: Path) -> None:
    code, env = run(["--out", "o", "--workflow", "wf"])
    assert (code, env["reason_codes"]) == (2, ["OCS_INPUT_INVALID"])
    assert "non-assurance" in env["messages"][0]
    diag = json.loads((work / "o" / "ci-diagnostics.json").read_text())
    assert (diag["exit_code"], diag["outcome"]) == (2, "invalid")


def test_refuses_both_profiles_at_once(work: Path) -> None:
    code, env = run(
        ["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json", "--non-assurance"]
    )
    assert code == 2
    assert "not both" in env["messages"][0]


def test_version_mismatch_exits_2_with_ocs_version_and_runs_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(monkeypatch, {})
    code, env = run(
        ["--out", "o", "--workflow", "wf", "--non-assurance", "--expect-version", "9.9.9"]
    )
    assert (code, env["reason_codes"], env["outcome"]) == (2, ["OCS_VERSION"], "invalid")
    assert fake.calls == []
    assert json.loads((work / "o" / "ci-diagnostics.json").read_text())["reason_codes"] == [
        "OCS_VERSION"
    ]
    assert (
        run(["--out", "o", "--workflow", "wf", "--non-assurance", "--expect-version", __version__])[
            0
        ]
        == 0
    )


def test_version_is_checked_before_the_profile(work: Path) -> None:
    code, env = run(["--out", "o", "--workflow", "wf", "--expect-version", "9.9.9"])
    assert (code, env["reason_codes"]) == (2, ["OCS_VERSION"])


@pytest.mark.parametrize("out", ["/etc/ocs-out", "../../../../../../../etc/x", "a\nb"])
def test_output_outside_the_workspace_is_refused(work: Path, out: str) -> None:
    code, env = run(["--out", out, "--workflow", "wf", "--non-assurance"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])


def test_input_with_crlf_or_missing_is_refused(work: Path) -> None:
    for bad in ("pack\n.json", "nope.json"):
        code, env = run(["--out", "o", "--evidence-pack", bad])
        assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    code, env = run(["--out", "o", "--workflow", "no-such-dir", "--non-assurance"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])


def test_input_inside_the_output_directory_is_refused(work: Path) -> None:
    (work / "o" / "reports").mkdir(parents=True)
    (work / "o" / "reports" / "pack.json").write_bytes((work / "pack.json").read_bytes())
    code, env = run(["--out", "o", "--evidence-pack", "o/reports/pack.json"])
    assert code == 2
    assert "inside the output directory" in env["messages"][0]


def test_output_inside_the_workflow_directory_is_refused(work: Path) -> None:
    code, env = run(["--out", "wf/out", "--workflow", "wf", "--non-assurance"])
    assert code == 2
    assert "inside the workflow directory" in env["messages"][0]


def test_output_directory_with_foreign_files_is_refused_and_untouched(work: Path) -> None:
    (work / "o").mkdir()
    (work / "o" / "mine.txt").write_text("keep")
    code, env = run(["--out", "o", "--workflow", "wf", "--non-assurance"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    assert sorted(p.name for p in (work / "o").iterdir()) == ["mine.txt"]


def test_symlinked_output_is_refused(work: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, work / "link")
    assert run(["--out", "link/o", "--workflow", "wf", "--non-assurance"])[0] == 2
    assert not any(target.iterdir())


def test_bad_report_mode_and_nothing_to_check(work: Path) -> None:
    assert main(["ci", "--out", "o", "--non-assurance", "--report-mode", "loud"]) == 2
    code, env = run(["--out", "o", "--non-assurance"])
    assert code == 2
    assert "nothing to check" in env["messages"][0]


# gating with scripted component outcomes


def test_non_assurance_never_passes(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Fake(monkeypatch, {})
    code, env = run(["--out", "o", "--workflow", "wf", "--non-assurance"])
    assert (code, env["outcome"]) == (0, "review")
    assert env["extensions"]["x-profile"] == "non-assurance"
    assert any("never pass" in m for m in env["messages"])
    assert main(["ci", "--out", "o", "--workflow", "wf", "--non-assurance", "--strict"]) == 1


def test_strict_evidence_all_pass_is_a_pass_and_copies_only_the_pack(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    code, env = run(["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"])
    assert (code, env["outcome"]) == (0, "pass")
    assert env["verdict_source"]["component"] == "governance-evidence-pack"
    assert env["does_not_prove"]
    names = sorted(
        p.relative_to(work / "o").as_posix() for p in (work / "o").rglob("*") if p.is_file()
    )
    assert names == [
        "ci-summary.json",
        "ci-summary.md",
        "evidence/evidence-pack.json",
        "reports/01-doctor.json",
        "reports/02-manifest.json",
        "reports/03-run-record.json",
        "reports/04-evidence-pack.json",
    ]
    assert (work / "o" / "evidence" / "evidence-pack.json").read_bytes() == (
        work / "pack.json"
    ).read_bytes()
    assert json.loads((work / "o" / "ci-summary.json").read_text())["outcome"] == "pass"


@pytest.mark.parametrize(
    ("outcomes", "expected", "code"),
    [
        ({"manifest": "invalid"}, "invalid", 2),
        ({"evidence-pack": "error"}, "error", 4),
        ({"run-record": "fail"}, "fail", 1),
        ({"doctor": "invalid"}, "invalid", 2),
        ({"manifest": "invalid", "run-record": "error"}, "error", 4),
        ({"manifest": "review"}, "review", 0),
    ],
)
def test_overall_outcome_is_the_worst_step(
    work: Path, monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, str], expected: str, code: int
) -> None:
    Fake(monkeypatch, outcomes)
    got, env = run(["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"])
    assert (env["outcome"], got) == (expected, code)
    if code:
        diag = json.loads((work / "o" / "ci-diagnostics.json").read_text())
        assert diag["exit_code"] == code
        assert "ci-summary.json" in diag["report_paths"]


def test_failed_pack_is_not_copied_to_evidence(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Fake(monkeypatch, {"evidence-pack": "invalid"})
    run(["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"])
    assert not (work / "o" / "evidence").exists()


def test_fail_fast_stops_at_the_first_failure_and_full_runs_everything(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(monkeypatch, {"manifest": "invalid"})
    args = ["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"]
    _code, env = run([*args, "--report-mode", "fail-fast"])
    assert fake.calls == ["doctor", "manifest"]
    assert [s["outcome"] for s in env["extensions"]["x-steps"]] == [
        "pass",
        "invalid",
        "not_run",
        "not_run",
    ]
    fake.calls.clear()
    _code, env = run([*args, "--report-mode", "full"])
    assert fake.calls == ["doctor", "manifest", "run-record", "evidence-pack"]
    assert env["verdict_source"]["component"] == "action-manifest"


def test_rerun_replaces_owned_files_and_drops_stale_reports(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    assert run(["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"])[0] == 0
    assert (work / "o" / "evidence").is_dir()
    assert run(["--out", "o", "--workflow", "wf", "--non-assurance"])[0] == 0
    assert not (work / "o" / "evidence").exists()
    assert not (work / "o" / "reports" / "04-evidence-pack.json").exists()


def test_copy_inputs_is_opt_in(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Fake(monkeypatch, {})
    base = ["--out", "o", "--workflow", "wf", "--manifest", "manifest.json", "--non-assurance"]
    run(base)
    assert not (work / "o" / "inputs").exists()
    run([*base, "--copy-inputs"])
    copied = sorted(p.name for p in (work / "o" / "inputs").iterdir())
    assert copied == ["01-manifest.json", "03-manifest.json", "04-run-record.json"]


def test_summary_markdown_neutralises_markup(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Fake(monkeypatch, {})
    weird = work / "a|b`c<d>[e](f)#g.json"
    weird.write_bytes((work / "manifest.json").read_bytes())
    run(["--out", "o", "--manifest", str(weird.name), "--non-assurance"])
    md = (work / "o" / "ci-summary.md").read_text()
    for char in "|`<>[]#":
        assert char not in md.split("| Check")[1].split("\n")[2].split("|", 3)[2]


def test_result_validates_against_the_envelope_schema(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import REPO_ROOT

    Fake(monkeypatch, {})
    schema = json.loads((REPO_ROOT / "schemas" / "v1" / "envelope.schema.json").read_text())
    validator = Draft202012Validator(schema)
    for argv in (
        ["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"],
        ["--out", "o", "--workflow", "wf", "--non-assurance"],
        ["--out", "o", "--workflow", "wf"],
        ["--out", "o", "--workflow", "wf", "--non-assurance", "--expect-version", "9.9.9"],
    ):
        _code, env = run(argv)
        assert [e.message for e in validator.iter_errors(env)] == []
    summary = json.loads((work / "o" / "ci-diagnostics.json").read_text())
    diag_schema = json.loads((REPO_ROOT / "schemas" / "v1" / "diagnostics.schema.json").read_text())
    assert Draft202012Validator(diag_schema).is_valid(summary)


# the real pinned components


def test_real_strict_evidence_run_passes(work: Path, stack: None) -> None:
    code, env = run(
        [
            "--out",
            "o",
            "--workflow",
            "wf",
            "--evidence-pack",
            "pack.json",
            "--expect-version",
            __version__,
        ]
    )
    assert (code, env["outcome"]) == (0, "pass"), env["messages"]
    assert [s["outcome"] for s in env["extensions"]["x-steps"]] == ["pass"] * 4
    assert (work / "o" / "evidence" / "evidence-pack.json").is_file()


def test_real_corrupted_inputs_exit_2_and_say_which_check(work: Path, stack: None) -> None:
    pack = json.loads((work / "pack.json").read_text())
    pack["pack_version"] = "9.9.9"
    (work / "bad-pack.json").write_text(json.dumps(pack))
    (work / "wf" / "run-record.json").write_text("{}")
    code, env = run(["--out", "o", "--workflow", "wf", "--evidence-pack", "bad-pack.json"])
    assert (code, env["outcome"]) == (2, "invalid")
    steps = {s["check"]: s for s in env["extensions"]["x-steps"]}
    assert steps["validate evidence pack"]["reason_codes"] == ["OCS_KIND_UNREGISTERED"]
    assert steps["validate workflow run record"]["outcome"] == "invalid"
    assert steps["validate workflow manifest"]["outcome"] == "pass"
    assert not (work / "o" / "evidence").exists()
    assert json.loads((work / "o" / "ci-diagnostics.json").read_text())["exit_code"] == 2
