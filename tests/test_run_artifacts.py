"""Run artifacts (items 7 and 8): one snapshot per input, validated and published from it, staged
output replaced only by a completed run, and no earlier or planted evidence left behind."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cognous_ocs import ci, doctor, fixtures_data, scaffold, validate
from cognous_ocs.cli import Outcome, main
from cognous_ocs.paths import PathRefused
from test_ci import Fake, run

STRICT = ["--out", "o", "--workflow", "wf", "--evidence-pack", "pack.json"]
OWNED_NAMES = {"ci-summary.json", "ci-summary.md", "ci-diagnostics.json", "reports", "evidence"}


@pytest.fixture
def work(ws: Path) -> Path:
    wf = ws / "wf"
    wf.mkdir()
    for name, content in scaffold.managed_bytes().items():
        (wf / name).write_bytes(content)
    (ws / "pack.json").write_bytes(fixtures_data.read_fixture("evidence-pack.json"))
    (ws / "manifest.json").write_bytes(fixtures_data.read_fixture("manifest.json"))
    return ws


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plant(work: Path) -> None:
    """A caller pre-populates a fake success in the output directory."""
    (work / "o" / "evidence").mkdir(parents=True)
    (work / "o" / "reports").mkdir()
    (work / "o/evidence/evidence-pack.json").write_text('{"planted": "fake success"}\n')
    (work / "o/reports/04-evidence-pack.json").write_text('{"outcome": "pass"}\n')
    (work / "o/ci-summary.json").write_text('{"outcome": "pass", "planted": true}\n')
    (work / "o/ci-summary.md").write_text("Outcome: **pass**\n")


def left(work: Path) -> set[str]:
    return {p.name for p in (work / "o").iterdir()}


def no_stale(work: Path, *, diagnostics: bool = True) -> None:
    assert left(work) <= {"ci-diagnostics.json"} if diagnostics else left(work) == set()


# item 7: validated bytes are published bytes


def test_validator_sees_a_private_snapshot_not_the_original_path(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    fake = Fake(monkeypatch, {})
    inner = fake.validate

    def spy(path: str, kind: str, **kw: Any) -> Outcome:
        seen.append(path)
        assert (Path(path).stat().st_mode & 0o777) == 0o400
        return inner(path, kind, **kw)

    monkeypatch.setattr(validate, "run_validate", spy)
    code, env = run(STRICT)
    assert code == 0
    assert len(seen) == 3
    for path in seen:
        assert not Path(path).is_relative_to(work)
        assert not Path(path).exists()  # the private directory is gone when the run ends
    shown = [s["path"] for s in env["extensions"]["x-steps"]]
    assert shown[-1] == "pack.json"
    report = json.loads((work / "o/reports/04-evidence-pack.json").read_text())
    assert report["subject"]["path"] == "pack.json"  # the original path, not the snapshot path


def test_mutation_after_validation_does_not_reach_the_published_copy(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (work / "pack.json").read_bytes()
    fake = Fake(monkeypatch, {})
    validated: dict[str, str] = {}

    def mutate(path: str, kind: str, **kw: Any) -> Outcome:
        out = fake.validate(path, kind, **kw)
        if kind == "evidence-pack":
            validated["sha"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            (work / "pack.json").write_bytes(b'{"tampered": "after validation"}\n')
        return out

    monkeypatch.setattr(validate, "run_validate", mutate)
    code, _env = run(STRICT)
    published = work / "o/evidence/evidence-pack.json"
    assert code == 0
    assert published.read_bytes() == original
    assert sha(published) == validated["sha"]
    assert (work / "pack.json").read_bytes() != original


def test_published_digest_equals_validated_digest_equals_summary_digest(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    code, env = run([*STRICT, "--copy-inputs"])
    assert code == 0
    marker = json.loads((work / "o/ci-summary.json").read_text())["extensions"]["x-run"]
    assert marker == env["extensions"]["x-run"]
    by_check = {i["check"]: i["sha256"] for i in marker["inputs"]}
    assert by_check["validate evidence pack"] == sha(work / "pack.json")
    published = {p["path"]: p["sha256"] for p in marker["published"]}
    assert published["evidence/evidence-pack.json"] == by_check["validate evidence pack"]
    assert sha(work / "o/evidence/evidence-pack.json") == published["evidence/evidence-pack.json"]
    for rel, digest in published.items():
        assert sha(work / "o" / rel) == digest
    assert {p.split("/")[0] for p in published} == {"evidence", "inputs"}


def test_a_snapshot_changed_after_validation_is_never_published(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(monkeypatch, {})

    def tamper(path: str, kind: str, **kw: Any) -> Outcome:
        out = fake.validate(path, kind, **kw)
        if kind == "evidence-pack":
            os.chmod(path, 0o600)
            Path(path).write_bytes(b'{"tampered": "snapshot"}\n')
        return out

    monkeypatch.setattr(validate, "run_validate", tamper)
    code, env = run(STRICT)
    assert (code, env["outcome"]) == (4, "error")
    assert not (work / "o/evidence").exists()
    assert "tampered" not in "".join(p.read_text() for p in (work / "o").rglob("*") if p.is_file())


def test_rename_swap_of_the_input_after_the_read_is_not_published(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (work / "pack.json").read_bytes()
    (work / "other.json").write_text('{"swapped": true}\n')
    fake = Fake(monkeypatch, {})

    def swap(path: str, kind: str, **kw: Any) -> Outcome:
        out = fake.validate(path, kind, **kw)
        if kind == "evidence-pack":
            os.replace(work / "other.json", work / "pack.json")  # rename over the input
        return out

    monkeypatch.setattr(validate, "run_validate", swap)
    code, _ = run(STRICT)
    assert code == 0
    assert (work / "o/evidence/evidence-pack.json").read_bytes() == original


def test_parent_directory_replacement_after_the_read_is_not_published(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (work / "inbox").mkdir()
    (work / "inbox/pack.json").write_bytes((work / "pack.json").read_bytes())
    original = (work / "pack.json").read_bytes()
    (work / "evil").mkdir()
    (work / "evil/pack.json").write_text('{"swapped": "parent"}\n')
    fake = Fake(monkeypatch, {})

    def swap_parent(path: str, kind: str, **kw: Any) -> Outcome:
        out = fake.validate(path, kind, **kw)
        if kind == "evidence-pack":
            os.rename(work / "inbox", work / "inbox.old")
            os.symlink(work / "evil", work / "inbox")
        return out

    monkeypatch.setattr(validate, "run_validate", swap_parent)
    code, _ = run(["--out", "o", "--evidence-pack", "inbox/pack.json"])
    assert code == 0
    assert (work / "o/evidence/evidence-pack.json").read_bytes() == original


def test_strict_path_refuses_a_symlinked_input(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake(monkeypatch, {})
    os.symlink(work / "pack.json", work / "linked.json")
    code, env = run(["--out", "o", "--workflow", "wf", "--evidence-pack", "linked.json"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    assert fake.calls == []
    assert not (work / "o/evidence").exists()


def test_strict_path_refuses_a_symlinked_parent_directory(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    (work / "real").mkdir()
    (work / "real/pack.json").write_bytes((work / "pack.json").read_bytes())
    os.symlink(work / "real", work / "dirlink")
    code, env = run(["--out", "o", "--evidence-pack", "dirlink/pack.json"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    assert not (work / "o/evidence").exists()


def test_open_input_walks_without_following_links(work: Path) -> None:
    (work / "d").mkdir()
    (work / "d/f.json").write_text("{}")
    os.close(ci._open_input(Path("d/f.json"), no_links=True))
    os.close(ci._open_input(work / "d" / "f.json", no_links=True))
    os.symlink(work / "d", work / "dl")
    os.symlink(work / "d/f.json", work / "fl")
    for bad in (Path("dl/f.json"), Path("fl"), work / "fl"):
        with pytest.raises(OSError):
            ci._open_input(bad, no_links=True)
    os.close(ci._open_input(Path("fl"), no_links=False))  # non-strict runs follow, as before


def test_positive_immutable_run_with_the_real_stack(work: Path, stack: None) -> None:
    code, env = run([*STRICT, "--copy-inputs"])
    assert (code, env["outcome"]) == (0, "pass"), env["messages"]
    marker = env["extensions"]["x-run"]
    assert (
        marker["nonce"] and len(marker["inputs"]) == 5
    )  # 3 validated + workflow.json + policy.json
    assert sha(work / "o/evidence/evidence-pack.json") == sha(work / "pack.json")
    assert sorted(p["path"] for p in marker["published"]) == [
        "evidence/evidence-pack.json",
        "inputs/02-manifest.json",
        "inputs/03-run-record.json",
    ]
    assert [p.name for p in work.iterdir() if p.name.startswith(".")] == []


def test_real_validator_gets_the_snapshot_and_the_report_names_the_original(
    work: Path, stack: None
) -> None:
    code, _env = run(["--out", "o", "--evidence-pack", "pack.json"])
    assert code == 0
    text = (work / "o/reports/01-evidence-pack.json").read_text()
    assert "ocs-run-" not in text


# item 8: nothing stale or planted survives a run that does not complete


def test_prepopulated_fake_success_then_a_refusal_leaves_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    plant(work)
    code, env = run([*STRICT, "--expect-version", "9.9.9"])
    assert (code, env["reason_codes"]) == (2, ["OCS_VERSION"])
    no_stale(work)
    assert json.loads((work / "o/ci-diagnostics.json").read_text())["exit_code"] == 2


@pytest.mark.parametrize(
    "extra",
    [
        ["--expect-version", "9.9.9"],
        ["--non-assurance"],  # both profiles at once
        ["--report-mode", "bogus"],
    ],
    ids=["version", "both-profiles", "report-mode"],
)
def test_every_early_refusal_clears_the_owned_output(
    work: Path, monkeypatch: pytest.MonkeyPatch, extra: list[str]
) -> None:
    Fake(monkeypatch, {})
    plant(work)
    code, _ = run([*STRICT, *extra])
    assert code == 2
    no_stale(work)


def test_a_rerun_failing_after_a_real_success_keeps_no_success_evidence(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(monkeypatch, {})
    assert run(STRICT)[0] == 0
    assert (work / "o/evidence/evidence-pack.json").is_file()
    fake.outcomes["evidence-pack"] = "invalid"
    code, _ = run(STRICT)
    assert code == 2
    assert not (work / "o/evidence").exists()
    summary = json.loads((work / "o/ci-summary.json").read_text())
    assert summary["outcome"] == "invalid"  # the summary is this run's, not the earlier pass


def test_prior_success_then_usage_error_leaves_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    assert run(STRICT)[0] == 0
    code, env = run([*STRICT, "--report-mode", "bogus"])  # argparse refuses before run_ci
    assert (code, env["reason_codes"]) == (2, ["OCS_INPUT_INVALID"])
    no_stale(work)
    assert json.loads((work / "o/ci-diagnostics.json").read_text())["exit_code"] == 2


def test_usage_error_with_options_in_any_form_still_clears(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    plant(work)
    assert main(["--format", "json", "ci", "--out=o", "--bogus-flag"]) == 2
    no_stale(work)
    plant(work)
    assert main(["ci", "--out", "o", "--report-mode", "x", "--strict"]) == 2
    no_stale(work)


def test_usage_error_for_other_commands_touches_nothing(work: Path) -> None:
    plant(work)
    assert main(["validate", "--bogus"]) == 2
    assert (work / "o/evidence/evidence-pack.json").is_file()


def test_prior_success_then_internal_exception_leaves_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    assert run(STRICT)[0] == 0

    def boom(*_a: Any, **_k: Any) -> Outcome:
        raise RuntimeError("forced")

    monkeypatch.setattr(validate, "run_validate", boom)
    code, env = run(STRICT)
    assert (code, env["outcome"], env["reason_codes"]) == (4, "error", ["OCS_INTERNAL"])
    no_stale(work)
    assert json.loads((work / "o/ci-diagnostics.json").read_text())["exit_code"] == 4
    assert [p.name for p in work.iterdir() if p.name.startswith(".")] == []


def test_internal_exception_outside_run_ci_still_clears(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    assert run(STRICT)[0] == 0

    def boom(*_a: Any, **_k: Any) -> Outcome:
        raise RuntimeError("forced")

    monkeypatch.setattr(ci, "run_ci", boom)
    assert run(STRICT)[0] == 4
    no_stale(work)


def test_partial_output_interruption_publishes_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    assert run(STRICT)[0] == 0
    real = ci.RunArtifacts.stage_snapshot

    def interrupted(self: Any, snap: Any, relative: str) -> None:
        real(self, snap, relative)
        raise KeyboardInterrupt

    monkeypatch.setattr(ci.RunArtifacts, "stage_snapshot", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(STRICT)
    assert left(work) == set()
    assert [p.name for p in work.iterdir() if p.name.startswith(".")] == []


def test_copy_failure_midway_publishes_nothing(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Fake(monkeypatch, {})
    plant(work)

    def disk_full(self: Any, snap: Any, relative: str) -> None:
        raise OSError("no space left")

    monkeypatch.setattr(ci.RunArtifacts, "stage_snapshot", disk_full)
    code, env = run(STRICT)
    assert (code, env["reason_codes"]) == (4, ["OCS_INTERNAL"])
    no_stale(work)


def test_killed_run_after_staging_leaves_no_prior_or_partial_output(work: Path) -> None:
    plant(work)
    code = (
        "import os, sys\n"
        "from cognous_ocs import ci, validate\n"
        "from cognous_ocs.cli import Outcome, main\n"
        "from cognous_ocs.envelope import Result\n"
        "def ok(path, kind, **_):\n"
        "    return Outcome(Result('validate', 'pass', [], {'kind': kind, 'path': path},"
        " does_not_prove=['x'], messages=['m']))\n"
        "validate.run_validate = ok\n"
        "ci.RunArtifacts.publish = lambda self: os._exit(9)\n"
        "sys.exit(main(['ci', '--out', 'o', '--evidence-pack', 'pack.json']))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=work, capture_output=True, text=True, check=False
    )
    assert done.returncode == 9, done.stderr
    assert left(work) == set()  # the planted output was cleared at the start, nothing was published


def test_old_owned_outputs_are_replaced_not_rejected(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    plant(work)
    code, _ = run(STRICT)
    assert code == 0
    assert json.loads((work / "o/ci-summary.json").read_text())["command"] == "ci"
    assert "planted" not in (work / "o/evidence/evidence-pack.json").read_text()
    assert (
        not (work / "o/reports/04-evidence-pack.json").read_text().startswith('{"outcome": "pass"}')
    )


def test_a_diagnostics_file_from_an_earlier_failure_does_not_survive_a_pass(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    assert run([*STRICT, "--expect-version", "9.9.9"])[0] == 2
    assert (work / "o/ci-diagnostics.json").is_file()
    assert run(STRICT)[0] == 0
    assert not (work / "o/ci-diagnostics.json").exists()


def test_foreign_files_still_refuse_and_are_not_touched(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    plant(work)
    (work / "o/notes.txt").write_text("mine\n")
    code, env = run(STRICT)
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    assert (work / "o/notes.txt").read_text() == "mine\n"
    # only the summary and diagnostics names go, so a stale pass summary cannot outlive the refusal;
    # the rest of a directory that holds foreign files is never touched
    assert not (work / "o/ci-summary.json").exists()
    assert not (work / "o/ci-summary.md").exists()
    assert (work / "o/evidence/evidence-pack.json").is_file()


def test_an_input_inside_an_owned_name_is_refused_and_survives(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    (work / "o/evidence").mkdir(parents=True)
    (work / "o/evidence/evidence-pack.json").write_bytes((work / "pack.json").read_bytes())
    (work / "o/reports").mkdir()
    (work / "o/reports/stale.json").write_text("{}")
    code, env = run(["--out", "o", "--evidence-pack", "o/evidence/evidence-pack.json"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    assert (work / "o/evidence/evidence-pack.json").is_file()  # the caller's input is not deleted
    assert not (work / "o/reports").exists()


def test_run_marker_carries_the_nonce_and_the_diagnostics_schema_is_unchanged(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jsonschema import Draft202012Validator

    from conftest import REPO_ROOT

    Fake(monkeypatch, {})
    _c, first = run(STRICT)
    _c, second = run(STRICT)
    assert first["extensions"]["x-run"]["nonce"] != second["extensions"]["x-run"]["nonce"]
    monkeypatch.setenv("OCS_RUN_NONCE", "ab" * 16)
    _c, third = run(STRICT)
    assert third["extensions"]["x-run"]["nonce"] == "ab" * 16
    monkeypatch.setenv("OCS_RUN_NONCE", "not a nonce")
    _c, fourth = run(STRICT)
    assert fourth["extensions"]["x-run"]["nonce"] != "not a nonce"
    run([*STRICT, "--expect-version", "9.9.9"])
    schema = json.loads((REPO_ROOT / "schemas/v1/diagnostics.schema.json").read_text())
    diag = json.loads((work / "o/ci-diagnostics.json").read_text())
    assert Draft202012Validator(schema).is_valid(diag)
    assert "x-run" not in diag and "nonce" not in json.dumps(diag)


def test_non_assurance_still_follows_a_linked_input(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    os.symlink(work / "manifest.json", work / "m-link.json")
    code, env = run(["--out", "o", "--manifest", "m-link.json", "--non-assurance"])
    assert code == 0
    assert env["extensions"]["x-run"]["inputs"][0]["sha256"] == sha(work / "manifest.json")


# strict no-link walk: absolute paths, differently spelled workspaces, temp roots


def make_pack_dir(base: Path, name: str, src: Path) -> Path:
    real = base / f"{name}-real"
    real.mkdir()
    (real / "pack.json").write_bytes(src.read_bytes())
    return real


def test_strict_refuses_an_absolute_path_outside_cwd_through_a_linked_parent(
    work: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    Fake(monkeypatch, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    real = make_pack_dir(outside, "d", work / "pack.json")
    os.symlink(real, outside / "link")
    code, env = run(["--out", "o", "--evidence-pack", str(outside / "link" / "pack.json")])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    # a linked middle component
    (outside / "g").mkdir()
    os.symlink(real, outside / "g" / "h")
    code, env = run(["--out", "o", "--evidence-pack", str(outside / "g" / "h" / "pack.json")])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    assert not (work / "o/evidence").exists()
    # the same file reached without a link is accepted
    code, _ = run(["--out", "o", "--evidence-pack", str(real / "pack.json")])
    assert code == 0


def test_strict_refuses_a_linked_directory_when_the_workspace_is_spelled_differently(
    work: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GITHUB_WORKSPACE names the workspace through a link; getcwd() is the physical path."""
    Fake(monkeypatch, {})
    spelled = tmp_path / "spelled"
    os.symlink(work, spelled)
    make_pack_dir(work, "ev", work / "pack.json")
    os.symlink(work / "ev-real", work / "evlink")  # repository content: a linked directory
    monkeypatch.setenv("GITHUB_WORKSPACE", str(spelled))
    code, env = run(["--out", "o", "--evidence-pack", str(spelled / "evlink" / "pack.json")])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    # the workspace link itself is the runner's: a plain file below it is accepted
    code, _ = run(["--out", "o", "--evidence-pack", str(spelled / "pack.json")])
    assert code == 0
    # with no root naming the link at all the walk starts at / and refuses it (fail closed)
    monkeypatch.delenv("GITHUB_WORKSPACE")
    monkeypatch.delenv("PWD", raising=False)
    code, env = run(["--out", "o", "--evidence-pack", str(spelled / "pack.json")])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])


def test_strict_accepts_a_path_under_a_linked_temp_root(
    work: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tempfile

    Fake(monkeypatch, {})
    real = tmp_path / "realtmp"
    real.mkdir()
    (real / "pack.json").write_bytes((work / "pack.json").read_bytes())
    os.symlink(real, tmp_path / "linktmp")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "linktmp"))
    code, _ = run(["--out", "o", "--evidence-pack", str(tmp_path / "linktmp" / "pack.json")])
    assert code == 0
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "linktmp"))
    code, _ = run(["--out", "o", "--evidence-pack", str(tmp_path / "linktmp" / "pack.json")])
    assert code == 0


def test_strict_walk_still_refuses_dotdot_through_a_link_and_the_final_link(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Fake(monkeypatch, {})
    make_pack_dir(work, "ev", work / "pack.json")
    os.symlink(work / "ev-real", work / "evlink")
    os.symlink(work / "pack.json", work / "final.json")
    for path in ("evlink/../evlink/pack.json", "evlink/pack.json", "final.json"):
        code, env = run(["--out", "o", "--evidence-pack", path])
        assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"]), path
        code, env = run(["--out", "o", "--evidence-pack", str(work / path)])
        assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"]), path


def test_an_input_swapped_for_a_fifo_does_not_hang_the_run(work: Path) -> None:
    os.mkfifo(work / "fifo.json")
    for no_links in (True, False):
        fd = ci._open_input(Path("fifo.json"), no_links=no_links)  # returns at once
        os.close(fd)
    arts = ci.RunArtifacts(work / "o")
    try:
        with pytest.raises(PathRefused):
            arts.snapshot(work / "fifo.json", "x.json", no_links=True)
    finally:
        arts.close()


# the doctor reads the run's copy of the workflow files


def test_doctor_reads_a_private_copy_not_the_live_workflow_files(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_checks = doctor.run_checks
    seen: dict[str, Any] = {}
    fake = Fake(monkeypatch, {})

    def corrupt_then_check(workflow: str | None, **kw: Any) -> Any:
        seen["dir"] = workflow
        (work / "wf/policy.json").write_text("{}")  # live files change after the snapshot
        (work / "wf/manifest.json").write_text("{}")
        (work / "wf/workflow.json").write_text("{}")
        checks = real_checks(workflow, **kw)
        seen["checks"] = {c.code: c.status for c in checks}
        return checks

    monkeypatch.setattr(ci, "run_checks", corrupt_then_check)
    code, env = run(["--out", "o", "--workflow", "wf", "--non-assurance"])
    assert seen["dir"] != "wf" and not Path(seen["dir"]).exists()  # private, gone after the run
    assert seen["checks"]["OCS_MANIFEST"] == "PASS"
    assert seen["checks"]["OCS_POLICY_BIND"] == "PASS"
    assert seen["checks"]["OCS_PATHS"] == "PASS"
    assert "ocs-run-" not in json.dumps(env)
    kinds = {i["kind"] for i in env["extensions"]["x-run"]["inputs"]}
    assert "workflow-setup" in kinds
    del fake, code


def test_doctor_copy_still_fails_a_workflow_that_is_wrong_at_the_start(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_checks = doctor.run_checks
    Fake(monkeypatch, {})
    (work / "wf/policy.json").write_text("{}")
    got: dict[str, str] = {}

    def check(workflow: str | None, **kw: Any) -> Any:
        checks = real_checks(workflow, **kw)
        got.update({c.code: c.status for c in checks})
        return checks

    monkeypatch.setattr(ci, "run_checks", check)
    run(["--out", "o", "--workflow", "wf", "--non-assurance"])
    assert got["OCS_POLICY_BIND"] == "FAIL"
