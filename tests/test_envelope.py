from __future__ import annotations

import json

import pytest

from cognous_ocs import envelope as env


def test_exit_code_map() -> None:
    assert env.exit_code_for(env.PASS) == 0
    assert env.exit_code_for(env.FAIL) == 1
    assert env.exit_code_for(env.INVALID) == 2
    assert env.exit_code_for(env.NOT_EVALUATED) == 3
    assert env.exit_code_for(env.ERROR) == 4


def test_review_exits_zero_by_default_and_one_when_strict() -> None:
    assert env.exit_code_for(env.REVIEW) == 0
    assert env.exit_code_for(env.REVIEW, strict=True) == 1
    assert env.Result("x", env.REVIEW, strict=True).exit_code == 1


def test_pass_requires_does_not_prove() -> None:
    with pytest.raises(ValueError, match="does not prove"):
        env.Result("x", env.PASS).to_dict()


def test_unknown_outcome_and_unprefixed_extension_refused() -> None:
    with pytest.raises(ValueError):
        env.Result("x", "maybe")
    with pytest.raises(ValueError, match="x-"):
        env.Result("x", env.FAIL, extensions={"steps": []})


def test_envelope_shape_and_version() -> None:
    r = env.Result(
        "validate",
        env.PASS,
        subject={"kind": "k"},
        verdict_source={"component": "c", "contract": "n", "contract_version": "1"},
        does_not_prove=["producer authenticity"],
        messages=["ok"],
        extensions={"x-note": 1},
    )
    body = json.loads(r.to_json())
    assert body["envelope_version"] == "1"
    assert body["exit_code"] == 0
    assert body["verdict_source"]["component"] == "c"
    assert body["extensions"] == {"x-note": 1}
    assert list(body)[:4] == ["envelope_version", "command", "outcome", "exit_code"]


def test_no_verdict_source_when_none() -> None:
    body = env.Result("x", env.INVALID).to_dict()
    assert "verdict_source" not in body


def test_messages_have_control_characters_stripped() -> None:
    nasty = "a\x1b[31mred\x00\r\nb" + chr(0x2028) + "c\x7fd\x9be"
    r = env.Result("x", env.FAIL, messages=[nasty])
    assert r.to_dict()["messages"] == ["a[31mredbcde"]
    assert "\x1b" not in r.to_text()


def test_strip_controls_keeps_printable_unicode() -> None:
    assert env.strip_controls("café ✓") == "café ✓"


def test_diagnostics_has_no_content(tmp_path: object) -> None:
    from pathlib import Path

    d = Path(str(tmp_path)) / "diag"
    r = env.Result("x", env.INVALID, reason_codes=["OCS_PATHS"], messages=["secret /etc/passwd"])
    out = env.write_diagnostics(r, d)
    body = json.loads(out.read_text())
    assert body == {
        "envelope_version": "1",
        "outcome": "invalid",
        "exit_code": 2,
        "reason_codes": ["OCS_PATHS"],
        "report_paths": [],
    }
    assert out.name == "ci-diagnostics.json"


def test_text_rendering_lists_boundary_and_source() -> None:
    r = env.Result(
        "demo",
        env.PASS,
        reason_codes=[],
        verdict_source={"component": "c", "contract": "n", "contract_version": "1"},
        does_not_prove=["a", "b"],
        messages=["done"],
    )
    text = r.to_text()
    assert "demo: pass (exit 0)" in text
    assert "does not prove: a; b" in text
    assert "verdict source: c (n 1)" in text
