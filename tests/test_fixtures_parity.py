"""Examples parity: bundled fixtures are byte-equal to their recorded hashes."""

from __future__ import annotations

import hashlib
import json

import pytest

from ocs import fixtures_data as fx


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
