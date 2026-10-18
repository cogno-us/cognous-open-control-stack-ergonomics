"""Examples parity: bundled fixtures are byte-equal to their recorded hashes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from ocs import fixtures_data as fx


def _derive() -> Any:
    """tools/derive_fixtures.py (needs only the standard library and ocs.lock to build inputs)."""
    path = Path(__file__).resolve().parents[1] / "tools" / "derive_fixtures.py"
    spec = importlib.util.spec_from_file_location("derive_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bundled_hashes_match_provenance() -> None:
    prov = fx.provenance()
    assert set(prov["bundled"]) == set(fx.FIXTURE_FILES)
    for name in fx.FIXTURE_FILES:
        assert hashlib.sha256(fx.read_fixture(name)).hexdigest() == prov["bundled"][name]


def test_manifest_is_byte_equal_to_its_recorded_source() -> None:
    prov = fx.provenance()
    assert prov["byte_equal_to_source"] == ["manifest.json"]
    assert prov["bundled"]["manifest.json"] == prov["sources"]["manifest.json"]["sha256"]


def test_derived_fixtures_differ_from_source_and_say_so() -> None:
    prov = fx.provenance()
    for name in ("run-record.json", "replay-bundle.json", "evidence-pack.json"):
        assert prov["bundled"][name] != prov["sources"][name]["sha256"]
    assert prov["synthetic"] is True


def test_provenance_records_hub_and_pins() -> None:
    from ocs.lock import COMPONENTS

    prov = fx.provenance()
    assert len(prov["hub"]["commit"]) == 40
    assert "README.md" in prov["hub"]["example_files"]
    by_key = {c.key: c for c in COMPONENTS.values()}
    for src in prov["sources"].values():
        assert src["commit"] == by_key[src["component"]].sha


def test_fixtures_are_valid_json_objects() -> None:
    for name in fx.FIXTURE_FILES:
        assert isinstance(json.loads(fx.read_fixture(name)), dict)


def test_tampered_fixture_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = fx.provenance()
    bad = json.loads(json.dumps(real))
    bad["bundled"]["manifest.json"] = "0" * 64
    monkeypatch.setattr(fx, "provenance", lambda: bad)
    with pytest.raises(fx.FixtureIntegrityError):
        fx.read_fixture("manifest.json")
    with pytest.raises(fx.FixtureIntegrityError):
        fx.read_fixture("nope.json")


def test_acp_run_inputs_match_provenance_and_are_not_copies() -> None:
    section = fx.provenance()["acp_run"]
    assert set(section["bundled"]) == set(fx.ACP_RUN_FILES)
    for name in fx.ACP_RUN_FILES:
        data = fx.read_acp_run_input(name)
        assert hashlib.sha256(data).hexdigest() == section["bundled"][name]
        assert isinstance(json.loads(data), dict)
    # reimplemented, not copied: no input equals a control-plane example file
    assert section["byte_equal_to_source"] == []
    assert not set(section["bundled"].values()) & set(section["modelled_on"].values())
    assert section["now"] == "2026-10-05T19:00:00Z"


def test_acp_run_inputs_bind_to_each_other() -> None:
    """The proposals commit to the bundled manifest, and the send is the uncovered permission."""
    derive = _derive()
    manifest = json.loads(fx.read_acp_run_input("manifest.json"))
    context = json.loads(fx.read_acp_run_input("authority-context.json"))
    for name in ("crm-read", "notes-search", "email-send"):
        proposal = json.loads(fx.read_acp_run_input(f"proposal-{name}.json"))
        assert proposal["manifest_digest"] == derive.commitment(manifest)
        assert proposal["payload_commitment"] == derive.commitment(proposal["payload"])
    granted = {p["action"] for p in context["grant"]["permissions"]}
    required = {p["action"] for p in context["requirement"]["permissions"]}
    assert required - granted == {"email_send"}
    send = json.loads(fx.read_acp_run_input("proposal-email-send.json"))
    assert send["requested_permissions"] == ["outbound.send"]


def test_acp_run_inputs_regenerate_byte_for_byte() -> None:
    derive = _derive()
    for name, obj in derive.acp_run_inputs().items():
        text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
        assert text.encode("utf-8") == fx.read_acp_run_input(name)


def test_control_plane_pin_records_the_gap_to_the_hub_lock() -> None:
    from ocs.lock import COMPONENTS

    pin = fx.provenance()["control_plane_pin"]
    assert pin["pinned_commit"] == COMPONENTS["acp"].sha
    assert pin["hub_component_lock_commit"] != pin["pinned_commit"]
    assert pin["ahead_of_hub_lock"] is True
    assert (
        fx.provenance()["hub"]["component_lock_control_plane_sha"]
        == pin["hub_component_lock_commit"]
    )


def test_unknown_acp_run_input_is_refused() -> None:
    with pytest.raises(fx.FixtureIntegrityError):
        fx.read_acp_run_input("nope.json")
