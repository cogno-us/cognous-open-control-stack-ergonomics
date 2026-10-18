"""The composite action: confinement helper, shell glue, pins, version binding, sample repo."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cognous_ocs import __version__
from cognous_ocs.paths import PathRefused, confine_output
from conftest import REPO_ROOT, load_script

ACTION = REPO_ROOT / ".github" / "actions" / "ocs"
check_action = load_script("check_action")
parity = load_script("check_examples_parity")


def load_helper() -> Any:
    spec = importlib.util.spec_from_file_location("confine_inputs", ACTION / "confine_inputs.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["confine_inputs"] = module
    spec.loader.exec_module(module)
    return module


helper = load_helper()


@pytest.fixture
def space(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace and a runner temp directory, with a sample workflow and pack inside."""
    workspace = tmp_path / "workspace"
    temp = tmp_path / "runner-temp"
    workspace.mkdir()
    temp.mkdir()
    shutil.copytree(REPO_ROOT / "examples" / "sample-repo", workspace / "sample")
    return workspace, temp


def env_for(**kw: str) -> dict[str, str]:
    base = {
        "OCS_IN_WORKFLOW": "sample/workflow",
        "OCS_IN_EVIDENCE_PACK": "sample/evidence/evidence-pack.json",
    }
    base.update({f"OCS_IN_{k.upper()}": v for k, v in kw.items()})
    return base


def plan(space: tuple[Path, Path], **kw: str) -> dict[str, str]:
    workspace, temp = space
    return helper.plan(env_for(**kw), workspace, temp)  # type: ignore[no-any-return]


def refused(space: tuple[Path, Path], **kw: str) -> Any:
    with pytest.raises(helper.Refused) as info:
        plan(space, **kw)
    return info.value


# the helper


def test_defaults_are_accepted_and_confined(space: tuple[Path, Path]) -> None:
    values = plan(space)
    assert values["OUT_DIR"] == str(space[0] / "ocs-out")
    assert (values["REPORT_MODE"], values["RETENTION_DAYS"], values["UPLOAD_FULL"]) == (
        "full",
        "14",
        "false",
    )
    assert values["INSTALL"] == "source"


def test_runner_temp_is_an_allowed_output_root(space: tuple[Path, Path]) -> None:
    out = str(space[1] / "reports")
    assert plan(space, out_dir=out)["OUT_DIR"] == out


@pytest.mark.parametrize("out", ["/etc/ocs", "../elsewhere", ".", "x/../../escape"])
def test_output_outside_or_at_a_root_is_refused(space: tuple[Path, Path], out: str) -> None:
    assert refused(space, out_dir=out).code == "OCS_PATHS"


@pytest.mark.parametrize(
    "field", ["manifest", "workflow", "evidence_pack", "out_dir", "upload_name", "report_mode"]
)
@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b"])
def test_cr_lf_nul_in_any_input_is_refused(space: tuple[Path, Path], field: str, bad: str) -> None:
    assert refused(space, **{field: bad}).code == "OCS_PATHS"


def test_symlinked_output_and_glob_characters_are_refused(
    space: tuple[Path, Path], tmp_path: Path
) -> None:
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", space[0] / "link")
    assert refused(space, out_dir="link/out").code == "OCS_PATHS"
    for glob in ("out*", "out?", "o[ut]", "!out", "out\\x"):
        assert refused(space, out_dir=glob).code == "OCS_PATHS"


def test_output_directory_with_foreign_files_is_refused(space: tuple[Path, Path]) -> None:
    out = space[0] / "ocs-out"
    out.mkdir()
    (out / "reports").mkdir()
    plan(space)  # only owned names: fine
    (out / "mine.txt").write_text("keep")
    assert refused(space).code == "OCS_PATHS"
    (out / "mine.txt").unlink()
    (space[0] / "file-not-dir").write_text("x")
    assert refused(space, out_dir="file-not-dir").code == "OCS_PATHS"


def test_missing_inputs_and_aliasing_are_refused(space: tuple[Path, Path]) -> None:
    assert refused(space, workflow="sample/nope").code == "OCS_PATHS"
    assert refused(space, evidence_pack="sample/nope.json").code == "OCS_PATHS"
    assert refused(space, workflow="sample/evidence/evidence-pack.json").code == "OCS_PATHS"
    out = space[0] / "ocs-out" / "reports"
    out.mkdir(parents=True)
    shutil.copy(space[0] / "sample" / "evidence" / "evidence-pack.json", out / "pack.json")
    assert (
        "aliases an owned output"
        in refused(space, evidence_pack="ocs-out/reports/pack.json").message
    )
    assert "inside an input directory" in refused(space, out_dir="sample/workflow/out").message
    assert refused(space, out_dir="sample").code == "OCS_PATHS"


def test_fail_closed_profile(space: tuple[Path, Path]) -> None:
    workspace, temp = space
    neither = helper_env(evidence_pack="")
    with pytest.raises(helper.Refused, match="exactly one"):
        helper.plan(neither, workspace, temp)
    both = helper_env(non_assurance="true")
    with pytest.raises(helper.Refused, match="exactly one"):
        helper.plan(both, workspace, temp)
    ok = helper.plan(helper_env(evidence_pack="", non_assurance="true"), workspace, temp)
    assert ok["NON_ASSURANCE"] == "true"
    with pytest.raises(helper.Refused, match="nothing to check"):
        helper.plan({"OCS_IN_NON_ASSURANCE": "true"}, workspace, temp)


def helper_env(**kw: str) -> dict[str, str]:
    return env_for(**kw)


@pytest.mark.parametrize(
    "bad",
    [
        {"report_mode": "loud"},
        {"install": "curl"},
        {"strict": "yes"},
        {"upload_reports": "1"},
        {"upload_full": "TRUE"},
        {"retention_days": "0"},
        {"retention_days": "91"},
        {"retention_days": "x"},
        {"retention_days": "-3"},
        {"upload_name": "../x"},
        {"upload_name": ""},
        {"upload_name": "a b"},
    ],
)
def test_enumerated_and_numeric_inputs_are_checked(
    space: tuple[Path, Path], bad: dict[str, str]
) -> None:
    if bad == {"upload_name": ""}:
        assert plan(space, **bad)["UPLOAD_NAME"] == "ocs-reports"  # empty means default
        return
    assert refused(space, **bad).code == "OCS_INPUT_INVALID"


@pytest.mark.parametrize(
    "raw",
    [
        "ok",
        "a/b",
        "a/../b",
        "../x",
        "/etc/x",
        "a\nb",
        "x/../../y",
        ".",
        "sub/./dir",
        "/var/elsewhere",
    ],
)
def test_helper_and_ocs_paths_agree_on_the_same_cases(
    space: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    workspace, temp = space
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("RUNNER_TEMP", str(temp))
    try:
        confine_output(raw)
        theirs = True
    except PathRefused:
        theirs = False
    try:
        helper.confine_output(raw, workspace, temp)
        mine = True
    except helper.Refused:
        mine = False
    # cognous_ocs.paths also allows the system temp dir; the helper is stricter by design (workspace and
    # runner temp only), so it may refuse more, never less.
    assert mine <= theirs


def test_helper_refuses_what_ocs_paths_refuses_for_links(
    space: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, temp = space
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("RUNNER_TEMP", str(temp))
    target = workspace / "real"
    target.mkdir()
    os.symlink(target, workspace / "sym")
    with pytest.raises(PathRefused):
        confine_output("sym/x")
    with pytest.raises(helper.Refused):
        helper.confine_output("sym/x", workspace, temp)
    (workspace / "f.txt").write_text("x")
    os.link(workspace / "f.txt", workspace / "g.txt")
    with pytest.raises(PathRefused):
        confine_output("g.txt")


def run_main(space: tuple[Path, Path], tmp_path: Path, **kw: str) -> tuple[int, str, Path]:
    workspace, temp = space
    output = tmp_path / "github-output"
    env = {
        **env_for(**kw),
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(temp),
        "GITHUB_OUTPUT": str(output),
    }
    import contextlib
    import io

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = helper.main(env)
    return code, err.getvalue(), output


def test_main_writes_validated_outputs(space: tuple[Path, Path], tmp_path: Path) -> None:
    code, err, output = run_main(space, tmp_path)
    assert (code, err) == (0, "")
    lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert lines["out-dir"] == str(space[0] / "ocs-out")
    assert lines["retention-days"] == "14"


def test_main_refusal_exits_2_never_echoes_the_input_and_leaves_diagnostics(
    space: tuple[Path, Path], tmp_path: Path
) -> None:
    code, err, output = run_main(space, tmp_path, retention_days="1\n::error::pwned")
    assert code == 2
    assert "pwned" not in err
    assert err.startswith("::error title=cognous-ocs action::")
    assert err.count("\n") == 1
    assert not output.exists()
    diag = json.loads((space[0] / "ocs-out" / "ci-diagnostics.json").read_text())
    assert (diag["exit_code"], diag["outcome"]) == (2, "invalid")


def test_main_refusal_with_a_bad_output_writes_nothing_outside(
    space: tuple[Path, Path], tmp_path: Path
) -> None:
    code, err, _ = run_main(space, tmp_path, out_dir="/etc/ocs-out")
    assert code == 2
    assert "etc" not in err
    assert not Path("/etc/ocs-out").exists()


def test_main_profile_refusal_is_exit_2(space: tuple[Path, Path], tmp_path: Path) -> None:
    code, err, _ = run_main(space, tmp_path, evidence_pack="")
    assert code == 2
    assert "non-assurance" in err
    diag = json.loads((space[0] / "ocs-out" / "ci-diagnostics.json").read_text())
    assert diag["reason_codes"] == ["OCS_INPUT_INVALID"]


# the shell glue, run for real


def step_script(step_id: str) -> str:
    action = yaml.safe_load((ACTION / "action.yml").read_text())
    return str(next(s for s in action["runs"]["steps"] if s.get("id") == step_id)["run"])


def run_step(step_id: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full = {"PATH": os.environ["PATH"], "GITHUB_ACTION_PATH": str(ACTION), **env}
    return subprocess.run(
        ["bash", "-c", step_script(step_id)], env=full, capture_output=True, text=True, check=False
    )


@pytest.fixture
def glue(tmp_path: Path, ws: Path) -> dict[str, str]:
    shutil.copytree(REPO_ROOT / "examples" / "sample-repo", ws / "sample")
    return {
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "OCS_OUT_DIR": str(ws / "ocs-out"),
        "OCS_REPORT_MODE": "full",
        "OCS_UPLOAD_FULL": "false",
        "OCS_MANIFEST": "",
        "OCS_WORKFLOW": "sample/workflow",
        "OCS_EVIDENCE_PACK": "sample/evidence/evidence-pack.json",
        "OCS_NON_ASSURANCE": "false",
        "OCS_STRICT": "false",
        "OCS_ACTION_BIN": str(Path(sys.executable).parent / "cognous-ocs"),
    }


def outputs(env: dict[str, str]) -> dict[str, str]:
    return dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())


def test_run_step_passes_with_the_real_stack_and_appends_the_summary(
    glue: dict[str, str], stack: None
) -> None:
    done = run_step("run", glue)
    assert done.returncode == 0, done.stdout + done.stderr
    assert outputs(glue)["exit-code"] == "0"
    assert "## cognous-ocs ci" in Path(glue["GITHUB_STEP_SUMMARY"]).read_text()
    assert (Path(glue["OCS_OUT_DIR"]) / "evidence" / "evidence-pack.json").is_file()
    assert not (Path(glue["OCS_OUT_DIR"]) / "inputs").exists()


def test_run_step_upload_full_copies_inputs(glue: dict[str, str], stack: None) -> None:
    run_step("run", {**glue, "OCS_UPLOAD_FULL": "true"})
    assert sorted(p.name for p in (Path(glue["OCS_OUT_DIR"]) / "inputs").iterdir()) == [
        "02-manifest.json",
        "03-run-record.json",
    ]


def test_run_step_always_passes_the_expected_version_and_a_wrong_cli_exits_2(
    glue: dict[str, str], tmp_path: Path
) -> None:
    fake = tmp_path / "fake-ocs"
    fake.write_text(
        "#!/bin/sh\n"
        "# stands in for a CLI of another version\n"
        'for a in "$@"; do [ "$prev" = "--expect-version" ] && exp="$a"; prev="$a"; done\n'
        'echo "installed 9.9.9, expected $exp"; [ "$exp" = "9.9.9" ] && exit 0; exit 2\n'
    )
    fake.chmod(0o755)
    done = run_step("run", {**glue, "OCS_ACTION_BIN": str(fake)})
    assert done.returncode == 0
    assert outputs(glue)["exit-code"] == "2"
    assert f"expected {__version__}" in done.stdout


def test_run_step_with_the_real_cli_and_a_mismatched_expectation(
    glue: dict[str, str], tmp_path: Path
) -> None:
    other = tmp_path / "act"
    shutil.copytree(ACTION, other)
    (other / "release-version.txt").write_text("9.9.9\n")
    env = {**glue, "GITHUB_ACTION_PATH": str(other)}
    full = {"PATH": os.environ["PATH"], **env}
    done = subprocess.run(
        ["bash", "-c", step_script("run")], env=full, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    assert outputs(glue)["exit-code"] == "2"
    diag = json.loads((Path(glue["OCS_OUT_DIR"]) / "ci-diagnostics.json").read_text())
    assert diag["reason_codes"] == ["OCS_VERSION"]


def test_run_step_missing_cli_is_not_a_pass(glue: dict[str, str]) -> None:
    run_step("run", {**glue, "OCS_ACTION_BIN": "/nonexistent/ocs"})
    assert outputs(glue)["exit-code"] == "127"
    assert run_step("propagate", {"OCS_EXIT": "127"}).returncode == 4


@pytest.mark.parametrize(
    ("code", "expected"),
    [("0", 0), ("1", 1), ("2", 2), ("3", 3), ("4", 4), ("9", 4), ("x", 4), ("", 4)],
)
def test_propagate_step_maps_exit_codes(code: str, expected: int) -> None:
    assert run_step("propagate", {"OCS_EXIT": code}).returncode == expected


# pins and hygiene


def test_repository_passes_the_action_check() -> None:
    assert check_action.run(REPO_ROOT) == []


def mutate(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


@pytest.mark.parametrize(
    ("old", "new", "expect"),
    [
        (
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
            "actions/checkout@v6",
            "not pinned",
        ),
        (
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
            "actions/checkout@main",
            "not pinned",
        ),
        (
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
            "release tag",
        ),
        (
            "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2",
            "actions/checkout@de0fac2e # v6.0.2",
            "not pinned",
        ),
        ("runs-on: ubuntu-24.04", "runs-on: ubuntu-latest", "runner image"),
    ],
)
def test_unpinned_actions_and_latest_runners_fail(
    repo_copy: Path, old: str, new: str, expect: str
) -> None:
    mutate(repo_copy, ".github/workflows/ci.yml", old, new)
    assert any(expect in e for e in check_action.check_pins(repo_copy))


def test_action_upload_pin_is_checked_too(repo_copy: Path) -> None:
    mutate(
        repo_copy,
        ".github/actions/ocs/action.yml",
        "@b7c566a772e6b6bfb58ed0dc250532a479d7789f # v6.0.0",
        "@v6",
    )
    assert any("action.yml" in e and "not pinned" in e for e in check_action.check_pins(repo_copy))


def test_expression_in_a_run_script_fails(repo_copy: Path) -> None:
    mutate(
        repo_copy,
        ".github/actions/ocs/action.yml",
        'bin="${OCS_ACTION_BIN:-cognous-ocs}"',
        'bin="${{ inputs.manifest }}"',
    )
    assert any("${{" in e for e in check_action.check_action(repo_copy))


def test_input_set_defaults_and_docs_are_checked(repo_copy: Path) -> None:
    mutate(repo_copy, ".github/actions/ocs/action.yml", "default: '14'", "default: '90'")
    mutate(
        repo_copy,
        ".github/actions/ocs/action.yml",
        "  upload-name:\n",
        "  extra-input:\n    default: ''\n  upload-name:\n",
    )
    mutate(repo_copy, "docs/action.md", "`upload-name`", "`renamed`")
    errors = check_action.check_action(repo_copy)
    assert any("retention-days must default" in e for e in errors)
    assert any("documented set" in e for e in errors)
    assert any("does not document input `upload-name`" in e for e in errors)


def test_minimal_upload_must_not_name_the_whole_output_directory(repo_copy: Path) -> None:
    mutate(
        repo_copy,
        ".github/actions/ocs/action.yml",
        "          ${{ steps.confine.outputs.out-dir }}/reports/\n",
        "          ${{ steps.confine.outputs.out-dir }}/\n",
    )
    assert any("minimal upload" in e for e in check_action.check_action(repo_copy))


def test_release_version_must_equal_the_package_version(repo_copy: Path) -> None:
    (repo_copy / ".github/actions/ocs/release-version.txt").write_text("9.9.9\n")
    errors = check_action.check_version(repo_copy)
    assert len(errors) == 2
    assert check_action.check_version(REPO_ROOT) == []
    assert (ACTION / "release-version.txt").read_text().strip() == __version__


def test_unreadable_action_is_reported(repo_copy: Path) -> None:
    (repo_copy / ".github/actions/ocs/action.yml").write_text(": : :\n- [")
    assert any("unreadable" in e for e in check_action.check_action(repo_copy))


# the workflow tests the action on the sample repo, with negative jobs


def test_workflow_runs_the_action_on_the_sample_and_has_negative_jobs() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    sample = json.dumps(jobs["action-sample"])
    assert "./.github/actions/ocs" in sample
    assert "examples/sample-repo/workflow" in sample
    assert "examples/sample-repo/evidence/evidence-pack.json" in sample
    negative = json.dumps(jobs["action-negative"])
    for needle in ("continue-on-error", "OCS_VERSION", "9.9.9", "non-assurance", "/etc/ocs-out"):
        assert needle in negative
    pythons = jobs["checks"]["strategy"]["matrix"]["python-version"]
    assert pythons == ["3.11", "3.12", "3.13"]


# the sample repository


def test_sample_repository_matches_its_sources() -> None:
    assert parity.check_sample(REPO_ROOT) == []


def test_sample_drift_is_found(repo_copy: Path) -> None:
    target = repo_copy / "examples/sample-repo/workflow/policy.json"
    target.write_bytes(target.read_bytes() + b" ")
    (repo_copy / "examples/sample-repo/workflow/extra.json").write_text("{}")
    (repo_copy / "examples/sample-repo/evidence/evidence-pack.json").write_text("{}")
    errors = parity.check_sample(repo_copy)
    assert any("policy.json: differs" in e for e in errors)
    assert any("extra.json: not part" in e for e in errors)
    assert any("evidence-pack.json: differs" in e for e in errors)
