"""`cognous-ocs init workflow`: idempotent, resumable, never replaces a differing file."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cognous_ocs import scaffold
from cognous_ocs.cli import main


def snapshot(directory: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
        for p in sorted(directory.iterdir())
    }


def test_fresh_scaffold_writes_exactly_the_managed_files(ws: Path) -> None:
    assert main(["init", "workflow", "--out", "w"]) == 0
    assert sorted(p.name for p in (ws / "w").iterdir()) == sorted(scaffold.MANAGED)
    for name, content in scaffold.managed_bytes().items():
        assert (ws / "w" / name).read_bytes() == content


def test_scaffold_is_deterministic_and_synthetic() -> None:
    assert scaffold.managed_bytes() == scaffold.managed_bytes()
    text = b"".join(scaffold.managed_bytes().values()).decode()
    assert "example.invalid" in text and "replace-with" in text
    manifest = json.loads(scaffold.managed_bytes()[scaffold.MANIFEST_FILE])
    assert manifest["metadata"]["synthetic"] is True


def test_rerun_with_identical_bytes_changes_nothing(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", "workflow", "--out", "w"]) == 0
    before = snapshot(ws / "w")
    capsys.readouterr()
    assert main(["--format", "json", "init", "workflow", "--out", "w"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert snapshot(ws / "w") == before, "bytes, mtimes and inodes are all unchanged"
    assert {f["status"] for f in body["extensions"]["x-files"]} == {"unchanged"}
    assert body["does_not_prove"]


def test_partial_scaffold_resumes_by_creating_only_the_missing_files(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["init", "workflow", "--out", "w"])
    (ws / "w" / scaffold.POLICY_FILE).unlink()
    (ws / "w" / scaffold.WORKFLOW_FILE).unlink()
    before = snapshot(ws / "w")
    capsys.readouterr()
    assert main(["--format", "json", "init", "workflow", "--out", "w"]) == 0
    body = json.loads(capsys.readouterr().out)
    after = snapshot(ws / "w")
    for name in (scaffold.MANIFEST_FILE, scaffold.RUN_RECORD_FILE):
        assert after[name] == before[name]
    statuses = {f["file"]: f["status"] for f in body["extensions"]["x-files"]}
    assert statuses[scaffold.POLICY_FILE] == statuses[scaffold.WORKFLOW_FILE] == "created"
    assert after[scaffold.POLICY_FILE][0] == scaffold.managed_bytes()[scaffold.POLICY_FILE]


def test_a_differing_file_stops_with_exit_2_before_anything_is_written(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["init", "workflow", "--out", "w"])
    policy = ws / "w" / scaffold.POLICY_FILE
    policy.write_text('{"mine": true}\n')
    (ws / "w" / scaffold.MANIFEST_FILE).unlink()  # missing file must NOT be created either
    before = snapshot(ws / "w")
    capsys.readouterr()
    assert main(["--format", "json", "init", "workflow", "--out", "w"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["reason_codes"] == ["OCS_PATHS"] and body["outcome"] == "invalid"
    assert "policy.json" in " ".join(body["messages"])
    assert snapshot(ws / "w") == before
    assert policy.read_text() == '{"mine": true}\n'


@pytest.mark.parametrize("trailing", [b" ", b"\n", b"\r\n"])
def test_even_a_whitespace_difference_is_a_conflict(ws: Path, trailing: bytes) -> None:
    main(["init", "workflow", "--out", "w"])
    target = ws / "w" / scaffold.WORKFLOW_FILE
    target.write_bytes(target.read_bytes() + trailing)
    assert main(["init", "workflow", "--out", "w"]) == 2
    assert target.read_bytes().endswith(trailing)


def test_symlink_directory_and_hardlink_in_place_of_a_file_are_conflicts(ws: Path) -> None:
    main(["init", "workflow", "--out", "w"])
    w = ws / "w"
    outside = ws / "outside.json"
    outside.write_text("keep")
    (w / scaffold.POLICY_FILE).unlink()
    (w / scaffold.POLICY_FILE).symlink_to(outside)
    assert main(["init", "workflow", "--out", "w"]) == 2
    assert outside.read_text() == "keep"
    (w / scaffold.POLICY_FILE).unlink()
    (w / scaffold.POLICY_FILE).mkdir()
    assert main(["init", "workflow", "--out", "w"]) == 2
    (w / scaffold.POLICY_FILE).rmdir()
    (w / scaffold.POLICY_FILE).write_bytes(scaffold.managed_bytes()[scaffold.POLICY_FILE])
    os.link(w / scaffold.POLICY_FILE, ws / "alias.json")
    assert main(["init", "workflow", "--out", "w"]) == 2


def test_output_that_is_a_file_or_outside_the_workspace_is_refused(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (ws / "afile").write_text("x")
    assert main(["init", "workflow", "--out", "afile"]) == 2
    assert main(["init", "workflow", "--out", "/etc/ocs-scaffold"]) == 2
    assert not Path("/etc/ocs-scaffold").exists()
    assert main(["init", "workflow", "--out", "a\nb"]) == 2
    assert capsys.readouterr().out.count("OCS_PATHS") == 3


def test_symlinked_output_directory_is_refused(ws: Path) -> None:
    (ws / "real").mkdir()
    (ws / "link").symlink_to(ws / "real")
    assert main(["init", "workflow", "--out", "link"]) == 2
    assert list((ws / "real").iterdir()) == []


def test_a_file_that_appears_mid_write_is_left_alone(
    ws: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def racing(path: Path, content: bytes) -> None:
        raise FileExistsError(str(path))

    monkeypatch.setattr(scaffold, "_create_exclusive", racing)
    assert main(["init", "workflow", "--out", "w"]) == 2
    assert "OCS_PATHS" in capsys.readouterr().out


def test_write_failure_is_an_internal_error(
    ws: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(path: Path, content: bytes) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(scaffold, "_create_exclusive", failing)
    assert main(["init", "workflow", "--out", "w"]) == 4
    assert "OCS_INTERNAL" in capsys.readouterr().out


def test_create_exclusive_never_truncates(ws: Path) -> None:
    target = ws / "f"
    target.write_text("original")
    with pytest.raises(FileExistsError):
        scaffold._create_exclusive(target, b"new")
    assert target.read_text() == "original"


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
def test_the_scaffold_passes_the_real_component_validators(ws: Path) -> None:
    assert main(["init", "workflow", "--out", "w"]) == 0
    assert main(["validate", "w/manifest.json", "--kind", "manifest"]) == 0
    assert main(["validate", "w/run-record.json", "--kind", "run-record"]) == 0
