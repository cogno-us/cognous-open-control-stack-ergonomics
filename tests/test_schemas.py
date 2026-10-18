"""`ocs schema export`, the v1 snapshots, and the snapshot immutability check."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from conftest import REPO_ROOT, load_script
from ocs import ENVELOPE_VERSION, schemas
from ocs.cli import main
from ocs.envelope import OUTCOMES, exit_code_for

snap = load_script("check_schema_snapshots")
SCHEMAS = REPO_ROOT / "schemas" / "v1"


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def run_json(argv: list[str]) -> tuple[int, dict[str, Any]]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main([*argv, "--format", "json"])
    return code, json.loads(buf.getvalue())


# export


def tree(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_export_equals_committed_snapshots_byte_for_byte(ws: Path) -> None:
    code, env = run_json(["schema", "export", "--out", "out"])
    assert code == 0
    assert env["outcome"] == "pass"
    assert env["does_not_prove"]
    assert tree(ws / "out") == tree(REPO_ROOT / "schemas")
    assert {f["path"] for f in env["extensions"]["x-files"]} == set(tree(ws / "out"))


def test_export_is_idempotent_and_leaves_identical_files_alone(ws: Path) -> None:
    assert main(["schema", "export", "--out", "out"]) == 0
    stamp = {p: p.stat().st_mtime_ns for p in (ws / "out").rglob("*") if p.is_file()}
    code, env = run_json(["schema", "export", "--out", "out"])
    assert code == 0
    assert "0 written" in env["messages"][0]
    assert stamp == {p: p.stat().st_mtime_ns for p in (ws / "out").rglob("*") if p.is_file()}


def test_export_resumes_a_partial_directory(ws: Path) -> None:
    assert main(["schema", "export", "--out", "out"]) == 0
    (ws / "out" / "v1" / "diagnostics.schema.json").unlink()
    assert main(["schema", "export", "--out", "out"]) == 0
    assert tree(ws / "out") == tree(REPO_ROOT / "schemas")


def test_export_refuses_a_differing_file_and_writes_nothing_else(ws: Path) -> None:
    (ws / "out" / "v1").mkdir(parents=True)
    (ws / "out" / "v1" / "envelope.schema.json").write_text("{}")
    code, env = run_json(["schema", "export", "--out", "out"])
    assert code == 2
    assert env["reason_codes"] == ["OCS_PATHS"]
    assert tree(ws / "out") == {
        "v1/envelope.schema.json": b"{}",
        "ci-diagnostics.json": (ws / "out" / "ci-diagnostics.json").read_bytes(),
    }


def test_export_refuses_paths_outside_and_symlinks(ws: Path, tmp_path: Path) -> None:
    code, env = run_json(["schema", "export", "--out", "/etc/ocs-schemas"])
    assert (code, env["reason_codes"]) == (2, ["OCS_PATHS"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, ws / "link")
    assert run_json(["schema", "export", "--out", "link/x"])[0] == 2
    assert not any(elsewhere.iterdir())
    assert run_json(["schema", "export", "--out", "a\nb"])[0] == 2


def test_schema_without_subcommand_is_a_usage_error(ws: Path) -> None:
    code, env = run_json(["schema"])
    assert (code, env["reason_codes"]) == (2, ["OCS_INPUT_INVALID"])


def test_damaged_bundle_is_never_exported(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = schemas.load_bundled

    def broken() -> Any:
        raise schemas.SnapshotError("v1/envelope.schema.json does not match its recorded hash")

    monkeypatch.setattr(schemas, "load_bundled", broken)
    code, env = run_json(["schema", "export", "--out", "out"])
    assert (code, env["outcome"], env["reason_codes"]) == (4, "error", ["OCS_INTERNAL"])
    assert set(tree(ws / "out")) <= {"ci-diagnostics.json"}
    monkeypatch.setattr(schemas, "load_bundled", real)


def test_load_bundled_checks_every_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "res"
    (root / "v1").mkdir(parents=True)
    (root / "v1" / "a.json").write_text("{}")
    manifest = {"schema": schemas.MANIFEST_SCHEMA, "snapshots": {"v1": {"a.json": "0" * 64}}}
    (root / "SNAPSHOTS.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(schemas, "_resource_root", lambda: root)
    with pytest.raises(schemas.SnapshotError, match="recorded hash"):
        schemas.load_bundled()
    (root / "SNAPSHOTS.json").write_text("{")
    with pytest.raises(schemas.SnapshotError, match="unreadable"):
        schemas.load_bundled()
    (root / "SNAPSHOTS.json").write_text(json.dumps({"schema": "other/9", "snapshots": {}}))
    with pytest.raises(schemas.SnapshotError, match="unknown schema"):
        schemas.load_bundled()


# the schemas describe what ocs really emits


def test_schema_constants_match_the_code() -> None:
    env = json.loads((SCHEMAS / "envelope.schema.json").read_text())
    assert env["properties"]["envelope_version"]["const"] == ENVELOPE_VERSION
    assert set(env["properties"]["outcome"]["enum"]) == OUTCOMES
    diag = json.loads((SCHEMAS / "diagnostics.schema.json").read_text())
    assert diag["properties"]["envelope_version"]["const"] == ENVELOPE_VERSION
    assert set(diag["properties"]["outcome"]["enum"]) == OUTCOMES - {"pass"}


@pytest.mark.parametrize("outcome", sorted(OUTCOMES))
@pytest.mark.parametrize("strict", [False, True])
def test_schema_accepts_exactly_the_exit_code_mapping(outcome: str, strict: bool) -> None:
    v = validator("envelope.schema.json")
    doc = {
        "envelope_version": "1",
        "command": "x",
        "outcome": outcome,
        "exit_code": exit_code_for(outcome, strict=strict),
        "reason_codes": [],
        "subject": {},
        "does_not_prove": ["something"],
        "messages": [],
    }
    assert v.is_valid(doc)
    for wrong in range(5):
        if wrong != doc["exit_code"] and not (outcome == "review" and wrong in (0, 1)):
            assert not v.is_valid({**doc, "exit_code": wrong})


def test_real_results_validate_against_the_envelope_schema(ws: Path) -> None:
    v = validator("envelope.schema.json")
    from ocs import fixtures_data

    (ws / "pack.json").write_bytes(fixtures_data.read_fixture("evidence-pack.json"))
    (ws / "bad.json").write_text("[]")
    runs = [
        ["schema", "export", "--out", "o"],
        ["schema", "export", "--out", "/etc/x"],
        ["evidence", "render", "pack.json", "--out", "v.html"],
        ["evidence", "render", "bad.json", "--out", "v2.html"],
        ["validate", "bad.json", "--kind", "manifest"],
        ["validate", "missing.json", "--kind", "manifest"],
        ["init", "workflow", "--out", "wf"],
        ["nope"],
        [],
    ]
    seen = set()
    for argv in runs:
        _code, env = run_json(argv)
        errors = sorted(e.message for e in v.iter_errors(env))
        assert errors == [], (argv, errors)
        seen.add(env["outcome"])
    assert {"pass", "invalid"} <= seen


def test_demo_result_validates_against_the_envelope_schema(ws: Path, stack: None) -> None:
    code, env = run_json(["demo", "customer-service", "--out", "d"])
    assert code == 0
    assert validator("envelope.schema.json").is_valid(env)
    assert env["verdict_source"]["component"]


def test_written_diagnostics_validate_against_the_diagnostics_schema(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "diag")
    assert main(["validate", "missing.json", "--kind", "manifest"]) == 2
    doc = json.loads((ws / "diag" / "ci-diagnostics.json").read_text())
    assert validator("diagnostics.schema.json").is_valid(doc)
    assert doc["exit_code"] == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(unknown=1),
        lambda d: d.update(envelope_version="2"),
        lambda d: d.update(outcome="maybe"),
        lambda d: d.update(outcome="pass", exit_code=0, does_not_prove=[]),
        lambda d: d.update(exit_code=3),
        lambda d: d.update(messages=["bad\x1bcontrol"]),
        lambda d: d.update(extensions={"no-prefix": 1}),
        lambda d: d.update(verdict_source={"component": "x"}),
        lambda d: d.pop("subject"),
    ],
)
def test_mutated_envelopes_are_refused(mutate: Any) -> None:
    doc: dict[str, Any] = {
        "envelope_version": "1",
        "command": "x",
        "outcome": "invalid",
        "exit_code": 2,
        "reason_codes": ["OCS_INPUT_INVALID"],
        "subject": {},
        "does_not_prove": [],
        "messages": ["fine"],
    }
    v = validator("envelope.schema.json")
    assert v.is_valid(doc)
    mutate(doc)
    assert not v.is_valid(doc)


# snapshot manifest and tag immutability


def test_repository_snapshots_pass_the_check() -> None:
    assert snap.check_manifest(REPO_ROOT) == []
    assert snap.check_tags(REPO_ROOT) == []
    assert set(snap.load_manifest(REPO_ROOT)) == {"v1"}


def test_mutating_a_recorded_snapshot_fails_the_check(repo_copy: Path) -> None:
    target = repo_copy / "schemas" / "v1" / "envelope.schema.json"
    target.write_bytes(target.read_bytes().replace(b"envelope", b"Envelope", 1))
    assert any("envelope.schema.json: bytes differ" in e for e in snap.check_manifest(repo_copy))
    assert snap.main(["--root", str(repo_copy)]) == 1


def test_added_removed_and_unlisted_files_fail_the_check(repo_copy: Path) -> None:
    v1 = repo_copy / "schemas" / "v1"
    (v1 / "extra.json").write_text("{}")
    (v1 / "diagnostics.schema.json").unlink()
    (repo_copy / "schemas" / "v2").mkdir()
    (repo_copy / "schemas" / "unreleased").mkdir()
    errors = snap.check_manifest(repo_copy)
    assert any("extra.json: file is not recorded" in e for e in errors)
    assert any("diagnostics.schema.json: recorded file is missing" in e for e in errors)
    assert any("schemas/v2/ is not recorded" in e for e in errors)
    assert not any("unreleased" in e for e in errors)


def test_missing_snapshot_directory_and_bad_manifest_fail(repo_copy: Path) -> None:
    manifest = repo_copy / "schemas" / "SNAPSHOTS.json"
    data = json.loads(manifest.read_text())
    data["snapshots"]["v7"] = {"a.json": "0" * 64}
    manifest.write_text(json.dumps(data))
    assert any("schemas/v7/ is recorded but missing" in e for e in snap.check_manifest(repo_copy))
    manifest.write_text("{")
    assert any("unreadable" in e for e in snap.check_manifest(repo_copy))
    manifest.write_text(json.dumps({"schema": "x", "snapshots": {}}))
    assert any("closed set" in e for e in snap.check_manifest(repo_copy))
    manifest.write_text(json.dumps({"schema": snap.SCHEMA_ID, "snapshots": {"latest": {}}}))
    assert any("look like v1" in e for e in snap.check_manifest(repo_copy))


def test_record_adds_a_new_snapshot_but_never_rerecords(repo_copy: Path) -> None:
    assert any("already recorded" in e for e in snap.record(repo_copy, "v1"))
    assert any("not a snapshot directory" in e for e in snap.record(repo_copy, "v9"))
    v2 = repo_copy / "schemas" / "v2"
    v2.mkdir()
    (v2 / "envelope.schema.json").write_text("{}")
    assert snap.record(repo_copy, "v2") == []
    assert snap.check_manifest(repo_copy) == []
    assert snap.main(["--root", str(repo_copy), "--record", "v2"]) == 1


def git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def test_tagged_snapshot_cannot_change_even_if_the_manifest_is_edited(repo_copy: Path) -> None:
    """In a temp git repo with a tag, editing the manifest cannot excuse a change."""
    git(repo_copy, "init", "-q", "-b", "main")
    git(repo_copy, "add", "schemas")
    git(repo_copy, "commit", "-q", "-m", "snapshots")
    git(repo_copy, "tag", "v0.0.1")
    assert snap.check_tags(repo_copy) == []
    target = repo_copy / "schemas" / "v1" / "envelope.schema.json"
    original = target.read_bytes()
    target.write_bytes(original + b" ")
    # re-record the mutated hash in the manifest: the manifest check now passes ...
    data = json.loads((repo_copy / "schemas" / "SNAPSHOTS.json").read_text())
    data["snapshots"]["v1"] = snap.hash_dir(repo_copy / "schemas" / "v1")
    (repo_copy / "schemas" / "SNAPSHOTS.json").write_text(json.dumps(data))
    assert snap.check_manifest(repo_copy) == []
    # ... and the tag comparison still refuses it
    assert any(
        "v0.0.1: schemas/v1/envelope.schema.json changed" in e for e in snap.check_tags(repo_copy)
    )
    assert snap.main(["--root", str(repo_copy)]) == 1
    target.write_bytes(original)
    (repo_copy / "schemas" / "v1" / "diagnostics.schema.json").unlink()
    assert any("was deleted" in e for e in snap.check_tags(repo_copy))


def test_a_new_snapshot_directory_after_a_tag_is_allowed(repo_copy: Path) -> None:
    git(repo_copy, "init", "-q", "-b", "main")
    git(repo_copy, "add", "schemas")
    git(repo_copy, "commit", "-q", "-m", "snapshots")
    git(repo_copy, "tag", "v0.0.1")
    v2 = repo_copy / "schemas" / "v2"
    v2.mkdir()
    (v2 / "envelope.schema.json").write_text("{}")
    assert snap.check_tags(repo_copy) == []
    assert snap.record(repo_copy, "v2") == []
    assert snap.main(["--root", str(repo_copy)]) == 0


def test_non_version_tags_are_not_compared(repo_copy: Path) -> None:
    git(repo_copy, "init", "-q", "-b", "main")
    git(repo_copy, "add", "schemas")
    git(repo_copy, "commit", "-q", "-m", "snapshots")
    git(repo_copy, "tag", "wip")
    (repo_copy / "schemas" / "v1" / "envelope.schema.json").write_bytes(b"{}")
    assert snap.check_tags(repo_copy) == []
