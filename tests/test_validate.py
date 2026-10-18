"""`cognous-ocs validate`: registry behaviour, exit mapping (fake components), real pinned components."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from cognous_ocs import fixtures_data as fx
from cognous_ocs import registry
from cognous_ocs.cli import main
from cognous_ocs.lock import COMPONENTS
from cognous_ocs.validate import run_validate

KIND_FILE = {
    "manifest": "manifest.json",
    "run-record": "run-record.json",
    "replay-bundle": "replay-bundle.json",
    "evidence-pack": "evidence-pack.json",
}


def pinned(distribution: str) -> str | None:
    return {c.distribution: c.sha for c in COMPONENTS.values()}.get(distribution)


def fake_bin(root: Path, bodies: dict[str, str] | None = None) -> Path:
    directory = root / "fakebin"
    directory.mkdir(exist_ok=True)
    for comp in COMPONENTS.values():
        script = directory / comp.command
        script.write_text((bodies or {}).get(comp.command, "#!/bin/sh\nexit 0\n"))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return directory


def doc_for(kind: str) -> dict[str, Any]:
    loaded = json.loads(fx.read_fixture(KIND_FILE[kind]))
    assert isinstance(loaded, dict)
    return loaded


def write(ws: Path, name: str, doc: object) -> str:
    path = ws / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return name


# ---- registry -------------------------------------------------------------


def test_every_kind_has_a_full_entry_and_every_entry_is_full_or_archival() -> None:
    for kind in registry.KINDS:
        assert any(k == kind and e.mode == registry.FULL for (k, _), e in registry.REGISTRY.items())
    for (kind, version), entry in registry.REGISTRY.items():
        assert (entry.kind, entry.version) == (kind, version)
        assert entry.mode in {registry.FULL, registry.ARCHIVAL_ONLY}
        if entry.mode == registry.FULL:
            assert entry.args, "a full entry names the component subcommand"


def test_bundled_fixtures_resolve_to_full_entries() -> None:
    for kind in registry.KINDS:
        version = registry.declared_version(kind, doc_for(kind))
        entry = registry.lookup(kind, version)
        assert entry is not None and entry.mode == registry.FULL


@pytest.mark.parametrize("value", [None, 1, 1.0, ["0.1"], {"v": "0.1"}, True])
def test_non_string_version_matches_nothing(value: object) -> None:
    for kind, key in registry.VERSION_KEY.items():
        assert registry.declared_version(kind, {key: value}) is None
    assert registry.declared_version("replay-bundle", {}) is None


def test_run_record_version_rules() -> None:
    assert registry.declared_version("run-record", {}) == registry.UNVERSIONED
    assert registry.declared_version("run-record", {"version": "2"}) == "2"
    assert registry.lookup("run-record", "2") is None
    assert registry.declared_version("run-record", {"version": 2}) is None
    # Two version-like members are ambiguous, which fails closed.
    assert registry.declared_version("run-record", {"version": "a", "schema_version": "a"}) is None


# ---- refusals before any component runs ----------------------------------


def run(ws: Path, name: str, kind: str, bin_dir: Path | None = None, **kw: Any) -> Any:
    outcome = run_validate(
        name, kind, bin_dir=bin_dir or fake_bin(ws.parent), commit_of=pinned, **kw
    )
    return outcome.result


def test_unregistered_version_fails_closed_exit_2(ws: Path) -> None:
    doc = doc_for("replay-bundle")
    doc["bundle_version"] = "9.9.9"
    result = run(ws, write(ws, "b.json", doc), "replay-bundle")
    assert result.exit_code == 2
    assert result.reason_codes == ["OCS_KIND_UNREGISTERED"]
    assert result.subject["declared_version"] == "9.9.9"
    assert result.verdict_source is None, "a registry refusal is not a component verdict"


def test_missing_version_fails_closed(ws: Path) -> None:
    doc = doc_for("evidence-pack")
    del doc["pack_version"]
    result = run(ws, write(ws, "p.json", doc), "evidence-pack")
    assert (result.exit_code, result.reason_codes) == (2, ["OCS_KIND_UNREGISTERED"])


def test_numeric_version_is_not_coerced_into_a_registered_string(ws: Path) -> None:
    doc = doc_for("manifest")
    doc["manifest_version"] = 1.0
    result = run(ws, write(ws, "m.json", doc), "manifest")
    assert (result.exit_code, result.reason_codes) == (2, ["OCS_KIND_UNREGISTERED"])


def test_archival_only_exits_2_with_its_own_code(ws: Path) -> None:
    doc = doc_for("replay-bundle")
    doc["bundle_version"] = "0.2.0"
    result = run(ws, write(ws, "b.json", doc), "replay-bundle")
    assert (result.exit_code, result.reason_codes) == (2, ["OCS_ARCHIVAL_ONLY"])
    assert result.outcome == "invalid"


def test_archival_only_never_runs_a_component(ws: Path) -> None:
    doc = doc_for("replay-bundle")
    doc["bundle_version"] = "0.2.0"
    boom = "#!/bin/sh\ntouch ran\nexit 0\n"
    result = run(ws, write(ws, "b.json", doc), "replay-bundle", fake_bin(ws.parent, {"arb": boom}))
    assert result.exit_code == 2
    assert not (ws / "ran").exists()


def test_kind_argument_decides_not_the_file(ws: Path) -> None:
    """An evidence pack handed to --kind replay-bundle is checked as a replay bundle."""
    result = run(ws, write(ws, "p.json", doc_for("evidence-pack")), "replay-bundle")
    assert result.exit_code == 2 and result.reason_codes == ["OCS_KIND_UNREGISTERED"]


def test_artifact_kind_inside_the_file_cannot_select_or_bypass(ws: Path) -> None:
    doc = doc_for("replay-bundle")
    doc["artifact_kind"] = "evidence-pack"
    result = run(ws, write(ws, "b.json", doc), "replay-bundle")
    assert (result.exit_code, result.reason_codes) == (2, ["OCS_INPUT_INVALID"])
    doc["artifact_kind"] = 7
    assert run(ws, write(ws, "b2.json", doc), "replay-bundle").exit_code == 2
    # Agreement is allowed (with or without underscores) and removing it changes nothing.
    doc["artifact_kind"] = "replay_bundle"
    assert run(ws, write(ws, "b3.json", doc), "replay-bundle").exit_code == 0
    del doc["artifact_kind"]
    assert run(ws, write(ws, "b4.json", doc), "replay-bundle").exit_code == 0


@pytest.mark.parametrize(
    "text",
    ["", "{", '{"bundle_version": "0.1"', "[]", "null", '{"a": 1, "a": 2}', '{"a": NaN}', "﻿{}"],
)
def test_unparseable_or_non_object_is_invalid(ws: Path, text: str) -> None:
    (ws / "x.json").write_text(text, encoding="utf-8")
    result = run(ws, "x.json", "replay-bundle")
    assert (result.exit_code, result.reason_codes) == (2, ["OCS_INPUT_INVALID"])


def test_invalid_utf8_and_missing_file_and_crlf_path(ws: Path) -> None:
    (ws / "x.json").write_bytes(b'{"a": "\xff"}')
    assert run(ws, "x.json", "manifest").reason_codes == ["OCS_INPUT_INVALID"]
    assert run(ws, "nope.json", "manifest").reason_codes == ["OCS_PATHS"]
    assert run(ws, "a\nb.json", "manifest").reason_codes == ["OCS_PATHS"]


def test_unknown_kind_is_refused_by_the_parser(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", "x.json", "--kind", "policy"]) == 2
    assert "OCS_INPUT_INVALID" in capsys.readouterr().out


# ---- exit mapping with fake components -----------------------------------


@pytest.mark.parametrize(
    ("body", "exit_code", "outcome", "reasons"),
    [
        ("exit 0", 0, "pass", []),
        ("echo bad; exit 1", 2, "invalid", ["OCS_INPUT_INVALID"]),
        ("echo cannot load >&2; exit 2", 2, "invalid", ["OCS_INPUT_INVALID"]),
        ("exit 3", 4, "error", ["OCS_INTERNAL"]),
        ("exit 139", 4, "error", ["OCS_INTERNAL"]),
        ("echo 'Traceback (most recent call last):' >&2; exit 1", 4, "error", ["OCS_INTERNAL"]),
    ],
)
def test_component_exit_is_mapped_without_inventing_a_verdict(
    ws: Path, body: str, exit_code: int, outcome: str, reasons: list[str]
) -> None:
    bin_dir = fake_bin(ws.parent, {"agep": f"#!/bin/sh\n{body}\n"})
    result = run(ws, write(ws, "p.json", doc_for("evidence-pack")), "evidence-pack", bin_dir)
    assert (result.exit_code, result.outcome, result.reason_codes) == (exit_code, outcome, reasons)
    assert result.verdict_source is not None
    assert result.verdict_source["component"] == "governance-evidence-pack"
    assert result.extensions["x-component-exit"] in {0, 1, 2, 3, 139}


def test_pass_states_what_it_does_not_prove(ws: Path) -> None:
    result = run(ws, write(ws, "p.json", doc_for("evidence-pack")), "evidence-pack")
    assert result.outcome == "pass"
    assert "artifact-internal validation passed" in result.messages[0]
    text = " ".join(result.does_not_prove)
    assert "external source linkage" in text and "producer authenticity" in text


def test_component_path_is_absolute_and_only_the_one_component_runs(ws: Path) -> None:
    log = ws.parent / "calls.log"
    body = f'#!/bin/sh\necho "$0 $@" >> {log}\nexit 0\n'
    bodies = {c.command: body for c in COMPONENTS.values()}
    run(ws, write(ws, "m.json", doc_for("manifest")), "manifest", fake_bin(ws.parent, bodies))
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and lines[0].endswith(f"aam validate {ws / 'm.json'}")


def test_unpinned_or_missing_component_is_a_version_failure(ws: Path) -> None:
    doc = write(ws, "m.json", doc_for("manifest"))
    out = run_validate(doc, "manifest", bin_dir=fake_bin(ws.parent), commit_of=lambda _d: "f" * 40)
    assert out.result.reason_codes == ["OCS_VERSION"] and out.result.exit_code == 2
    empty = ws.parent / "empty"
    empty.mkdir()
    out = run_validate(doc, "manifest", bin_dir=empty, commit_of=pinned)
    assert out.result.reason_codes == ["OCS_VERSION"]


def test_only_the_needed_component_is_pin_checked(ws: Path) -> None:
    bin_dir = fake_bin(ws.parent)
    (bin_dir / "arb").unlink()
    result = run(ws, write(ws, "m.json", doc_for("manifest")), "manifest", bin_dir)
    assert result.exit_code == 0


def test_timeout_is_an_internal_error(ws: Path) -> None:
    bin_dir = fake_bin(ws.parent, {"agep": "#!/bin/sh\nsleep 5\n"})
    result = run(
        ws, write(ws, "p.json", doc_for("evidence-pack")), "evidence-pack", bin_dir, timeout=0.2
    )
    assert (result.exit_code, result.reason_codes) == (4, ["OCS_INTERNAL"])


def test_json_envelope_for_pass_and_refusal(
    ws: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = doc_for("replay-bundle")
    doc["bundle_version"] = "9.9.9"
    name = write(ws, "b.json", doc)
    assert main(["--format", "json", "validate", name, "--kind", "replay-bundle"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["envelope_version"] == "1" and body["command"] == "validate"
    assert body["outcome"] == "invalid" and body["exit_code"] == 2
    assert body["reason_codes"] == ["OCS_KIND_UNREGISTERED"]
    assert body["subject"] == {"kind": "replay-bundle", "path": name, "declared_version": "9.9.9"}


def test_diagnostics_written_on_refusal_when_declared(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "diag")
    doc = doc_for("manifest")
    doc["manifest_version"] = "9"
    assert main(["validate", write(ws, "m.json", doc), "--kind", "manifest"]) == 2
    body = json.loads((ws / "diag" / "ci-diagnostics.json").read_text())
    assert body["reason_codes"] == ["OCS_KIND_UNREGISTERED"]
    assert "m.json" not in json.dumps(body)


# ---- real pinned components (integration) --------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
@pytest.mark.parametrize("kind", registry.KINDS)
def test_real_components_accept_the_bundled_artifacts(
    ws: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    name = write(ws, "a.json", doc_for(kind))
    assert main(["--format", "json", "validate", name, "--kind", kind]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["outcome"] == "pass"
    assert "artifact-internal validation passed" in body["messages"][0]
    assert "producer authenticity" in " ".join(body["does_not_prove"])
    assert body["verdict_source"]["component"]


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
def test_real_replay_bundle_corruption_exits_2(ws: Path) -> None:
    text = json.dumps(doc_for("replay-bundle"))
    (ws / "truncated.json").write_text(text[: len(text) // 2], encoding="utf-8")
    assert main(["validate", "truncated.json", "--kind", "replay-bundle"]) == 2
    doc = doc_for("replay-bundle")
    doc["action_proposals"] = "not-a-list"
    write(ws, "typed.json", doc)
    assert main(["validate", "typed.json", "--kind", "replay-bundle"]) == 2
    doc = doc_for("replay-bundle")
    doc["run_id"] = ""
    write(ws, "semantic.json", doc)
    assert main(["validate", "semantic.json", "--kind", "replay-bundle"]) == 2


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
def test_real_component_validator_is_version_blind_so_the_registry_matters(ws: Path) -> None:
    """arb validate accepts any bundle_version string; cognous-ocs refuses unknown ones first."""
    import subprocess
    import sys

    doc = doc_for("replay-bundle")
    doc["bundle_version"] = "9.9.9"
    name = write(ws, "b.json", doc)
    arb = Path(sys.executable).parent / "arb"
    assert (
        subprocess.run([str(arb), "validate", name], check=False, capture_output=True).returncode
        == 0
    )
    assert main(["validate", name, "--kind", "replay-bundle"]) == 2


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
def test_real_manifest_11_is_routed_to_aam_and_its_rules_apply(ws: Path) -> None:
    doc = doc_for("manifest")
    doc["manifest_version"] = "1.1"
    assert main(["validate", write(ws, "m.json", doc), "--kind", "manifest"]) == 2
    result = run_validate("m.json", "manifest")
    assert result.result.reason_codes == ["OCS_INPUT_INVALID"]
    assert "E018" in result.result.extensions["x-component-output"]
