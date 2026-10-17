from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from ocs import fixtures_data as fx
from ocs import render
from ocs.cli import main


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def pack_dict() -> dict[str, Any]:
    data = json.loads(fx.read_fixture("evidence-pack.json"))
    assert isinstance(data, dict)
    return data


def write_pack(ws: Path, data: dict[str, Any], name: str = "pack.json") -> str:
    (ws / name).write_text(json.dumps(data), encoding="utf-8")
    return name


def run_render(ws: Path, pack: str, out: str = "view.html") -> int:
    return main(["evidence", "render", pack, "--out", out])


def test_renders_bundled_pack_showing_the_blocked_send(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = write_pack(ws, pack_dict())
    assert run_render(ws, pack) == 0
    page = (ws / "view.html").read_text(encoding="utf-8")
    assert "chip blocked" in page
    assert "<code>email_send</code>" in page
    assert "outbound-send authority is missing" in page
    assert "Declared actions" in page and "Allowed" in page and "Reliance" in page
    assert "Replay inventory" in page and "does not prove" in page
    assert "bundle-cs-demo-001" in page
    out = capsys.readouterr().out
    assert "not validated" in out


def test_page_is_self_contained(ws: Path) -> None:
    run_render(ws, write_pack(ws, pack_dict()))
    page = (ws / "view.html").read_text(encoding="utf-8")
    assert "<script" not in page.lower()
    assert not re.search(r"(src|href)\s*=", page, flags=re.I)
    assert "http://" not in page and "https://" not in page
    assert "<style>" in page
    assert "default-src 'none'" in page


def test_every_pack_value_is_escaped(ws: Path) -> None:
    data = pack_dict()
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'&"
    data["title"] = evil
    data["blocked_actions"][0]["reason_summary"] = evil
    data["blocked_actions"][0]["action_name"] = evil
    data["action_inventory"][2]["action_name"] = evil
    data["reliance_summary"][0]["scope_summary"] = evil
    data["replay_bundles"][0]["bundle_id"] = evil
    run_render(ws, write_pack(ws, data))
    page = (ws / "view.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_control_characters_are_stripped_from_the_page(ws: Path) -> None:
    data = pack_dict()
    data["blocked_actions"][0]["reason_summary"] = "no\x1b[2Jauthority\x00\x07"
    run_render(ws, write_pack(ws, data))
    page = (ws / "view.html").read_text(encoding="utf-8")
    assert "no[2Jauthority" in page
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", page)


def test_non_dict_members_are_refused(ws: Path) -> None:
    data = pack_dict()
    data["blocked_actions"] = ["not an object"]
    assert run_render(ws, write_pack(ws, data)) == 2
    assert not (ws / "view.html").exists()


def test_unregistered_version_fails_closed(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = pack_dict()
    data["pack_version"] = "9.9.9"
    assert (
        main(["--format", "json", "evidence", "render", write_pack(ws, data), "--out", "v.html"])
        == 2
    )
    body = json.loads(capsys.readouterr().out)
    assert body["reason_codes"] == ["OCS_KIND_UNREGISTERED"]
    assert body["outcome"] == "invalid"
    assert not (ws / "v.html").exists()
    diag = json.loads((ws / "ci-diagnostics.json").read_text())
    assert diag["exit_code"] == 2


@pytest.mark.parametrize(
    "content",
    [b"not json", b"[1, 2]", b"\xff\xfe", b'{"pack_id": "x"}', b'{"pack_version": "0.1"}'],
)
def test_malformed_pack_is_invalid(ws: Path, content: bytes) -> None:
    (ws / "bad.json").write_bytes(content)
    assert run_render(ws, "bad.json") == 2
    assert not (ws / "view.html").exists()


def test_oversized_pack_is_refused(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "MAX_PACK_BYTES", 10)
    assert run_render(ws, write_pack(ws, pack_dict())) == 2


def test_output_outside_workspace_refused(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = write_pack(ws, pack_dict())
    assert (
        main(["--format", "json", "evidence", "render", pack, "--out", "/etc/ocs-view.html"]) == 2
    )
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["OCS_PATHS"]
    assert not Path("/etc/ocs-view.html").exists()


def test_input_with_newline_refused(ws: Path) -> None:
    write_pack(ws, pack_dict(), "p\nq.json")
    assert run_render(ws, "p\nq.json") == 2


def test_missing_input_refused(ws: Path) -> None:
    assert run_render(ws, "nope.json") == 2


def test_output_may_not_alias_input(ws: Path) -> None:
    pack = write_pack(ws, pack_dict())
    before = (ws / pack).read_bytes()
    assert run_render(ws, pack, out=pack) == 2
    assert (ws / pack).read_bytes() == before


def test_symlinked_output_refused(ws: Path, tmp_path: Path) -> None:
    pack = write_pack(ws, pack_dict())
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    (ws / "link.html").symlink_to(target)
    assert run_render(ws, pack, out="link.html") == 2
    assert target.read_text() == "keep"


def test_json_envelope_for_success(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack = write_pack(ws, pack_dict())
    assert main(["evidence", "render", pack, "--out", "view.html", "--format", "json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["envelope_version"] == "1"
    assert body["command"] == "evidence render"
    assert body["outcome"] == "pass"
    assert body["subject"]["declared_version"] == "0.1"
    assert "verdict_source" not in body
    assert body["does_not_prove"]


def test_classify_marks_unmatched_non_allowed_as_declared() -> None:
    data = {
        "action_inventory": [
            {"action_name": "a", "default_posture": "allowed"},
            {"action_name": "b", "default_posture": "escalate"},
            {"action_name": "c", "default_posture": "blocked"},
        ],
        "blocked_actions": [{"action_name": "c"}],
    }
    assert render.classify(data) == {"a": "allowed", "b": "declared", "c": "blocked"}


def test_empty_sections_say_so() -> None:
    page = render.render_html({"pack_id": "p", "pack_version": "0.1"})
    assert "declares no actions" in page
    assert "records no blocked actions" in page
    assert "no reliance sources" in page
    assert "no replay bundles" in page


def test_evidence_without_subcommand_is_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["evidence"]) == 2
    assert "OCS_INPUT_INVALID" in capsys.readouterr().out
