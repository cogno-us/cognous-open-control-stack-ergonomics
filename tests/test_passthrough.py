"""Passthrough wrappers: argument routing, exit relay, envelope only under --format json."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from helpers import fake_bin, pinned
from ocs import fixtures_data as fx
from ocs import passthrough
from ocs.cli import main
from ocs.lock import COMPONENTS

ECHO = '#!/bin/sh\necho "out: $@"\necho "err: $@" >&2\nexit {code}\n'


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., Path]:
    def make(code: int, body: str | None = None) -> Path:
        text = (body or ECHO).format(code=code)
        directory = fake_bin(tmp_path, {c.command: text for c in COMPONENTS.values()})
        monkeypatch.setattr(passthrough, "check_pins", lambda _c, _b=None, _f=None: directory)
        return directory

    return make


# ---- routing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "layer", "rest", "fmt"),
    [
        (["manifest", "validate", "m.json"], "aam", ["validate", "m.json"], "text"),
        (
            ["replay", "verify", "b", "--secret", "k"],
            "arb",
            ["verify", "b", "--secret", "k"],
            "text",
        ),
        (["--format", "json", "evidence", "summarize", "p"], "agep", ["summarize", "p"], "json"),
        (["--format=json", "--strict", "manifest"], "aam", [], "json"),
        (
            ["manifest", "validate", "m", "--format", "json"],
            "aam",
            ["validate", "m", "--format", "json"],
            "text",
        ),
        (["evidence", "validate", "p"], "agep", ["validate", "p"], "text"),
    ],
)
def test_detect_routes_everything_after_the_layer_to_the_component(
    argv: list[str], layer: str, rest: list[str], fmt: str
) -> None:
    pt = passthrough.detect(argv)
    assert pt is not None
    assert (pt.command, pt.rest, pt.fmt) == (layer, rest, fmt)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["demo", "customer-service", "--out", "d"],
        ["validate", "x", "--kind", "manifest"],
        ["evidence", "render", "p", "--out", "o"],
        ["evidence"],
        ["evidence", "--format", "json", "render", "p", "--out", "o"],
        ["--format", "yaml", "manifest", "validate"],
        ["--bogus", "manifest"],
    ],
)
def test_detect_leaves_everything_else_to_ocs(argv: list[str]) -> None:
    assert passthrough.detect(argv) is None


# ---- text mode: relay ------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, 2, 3, 4])
def test_text_mode_relays_output_and_exit_code(
    fake: Callable[..., Path], capsys: pytest.CaptureFixture[str], code: int
) -> None:
    fake(code)
    assert main(["manifest", "validate", "m.json"]) == code
    captured = capsys.readouterr()
    assert captured.out == "out: validate m.json\n"
    assert captured.err == "err: validate m.json\n"


def test_an_undocumented_exit_code_is_reported_as_4_with_a_note(
    fake: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake(7)
    assert main(["replay", "validate", "b"]) == 4
    assert "not a documented verdict" in capsys.readouterr().err


def test_ocs_options_after_the_layer_belong_to_the_component(
    fake: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake(0)
    assert main(["manifest", "validate", "m.json", "--format", "json"]) == 0
    assert capsys.readouterr().out == "out: validate m.json --format json\n"


def test_control_characters_in_component_output_are_stripped(
    fake: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake(0, "#!/bin/sh\nprintf 'a\\033[31mb\\tc\\n'\nexit {code}\n")
    assert main(["manifest", "x"]) == 0
    assert capsys.readouterr().out == "a[31mb\tc\n"


def test_large_output_is_relayed_whole_in_text_mode(
    fake: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    loop = "while [ $i -lt 1000 ]; do printf '%0100d' 0; i=$((i+1)); done"
    body = f"#!/bin/sh\ni=0\n{loop}\necho\nexit {{code}}\n"
    fake(0, body)
    assert main(["evidence", "validate", "p"]) == 0
    assert len(capsys.readouterr().out) == 100_001


# ---- json mode: envelope ---------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "exit_code", "outcome", "reason"),
    [
        (0, 0, "pass", None),
        (1, 1, "fail", "OCS_BLOCKING_FINDING"),
        (2, 2, "invalid", "OCS_INPUT_INVALID"),
        (3, 3, "not_evaluated", "OCS_NOT_APPLICABLE"),
        (4, 4, "error", "OCS_INTERNAL"),
        (9, 4, "error", "OCS_INTERNAL"),
    ],
)
def test_json_mode_wraps_the_run_in_the_envelope(
    fake: Callable[..., Path],
    capsys: pytest.CaptureFixture[str],
    code: int,
    exit_code: int,
    outcome: str,
    reason: str | None,
) -> None:
    fake(code)
    assert (
        main(["--format", "json", "replay", "verify", "b.json", "--secret", "hunter2"]) == exit_code
    )
    out = capsys.readouterr()
    body = json.loads(out.out)  # stdout is exactly one JSON object: no relayed component text
    assert out.err == ""
    assert body["envelope_version"] == "1" and body["command"] == "replay verify"
    assert (body["outcome"], body["exit_code"]) == (outcome, exit_code)
    assert body["reason_codes"] == ([reason] if reason else [])
    assert body["verdict_source"]["component"] == "replay-bundle"
    assert body["extensions"]["x-component-exit"] == code
    assert "out: verify b.json" in body["extensions"]["x-component-output"]
    if outcome == "pass":
        assert body["does_not_prove"]


def test_the_label_never_carries_arguments(
    fake: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fake(0)
    main(["--format", "json", "replay", "--secret", "hunter2", "verify"])
    assert json.loads(capsys.readouterr().out)["command"] == "replay"


# ---- failures before or around the run ------------------------------------------------


def test_unpinned_component_is_a_version_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pt = passthrough.detect(["manifest", "validate", "m"])
    assert pt is not None
    result, relayed = passthrough.execute(
        pt, bin_dir=fake_bin(tmp_path), commit_of=lambda _d: "f" * 40
    )
    assert (result.exit_code, result.reason_codes, relayed) == (2, ["OCS_VERSION"], False)
    result, _ = passthrough.execute(pt, bin_dir=tmp_path / "empty", commit_of=pinned)
    assert result.reason_codes == ["OCS_VERSION"]


def test_version_failure_is_printed_by_ocs_in_text_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    assert main(["manifest", "validate", "m"]) == 2
    assert "OCS_VERSION" in capsys.readouterr().out


def test_timeout_is_an_internal_error(tmp_path: Path) -> None:
    pt = passthrough.detect(["evidence", "validate", "p"])
    assert pt is not None
    directory = fake_bin(tmp_path, {"agep": "#!/bin/sh\nsleep 5\n"})
    result, _ = passthrough.execute(pt, bin_dir=directory, commit_of=pinned, timeout=0.2)
    assert (result.exit_code, result.reason_codes) == (4, ["OCS_INTERNAL"])


def test_diagnostics_are_written_on_a_nonzero_exit_when_declared(
    fake: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("OCS_DIAGNOSTICS_DIR", "diag")
    fake(1)
    assert main(["manifest", "validate", "m.json"]) == 1
    body = json.loads((work / "diag" / "ci-diagnostics.json").read_text())
    assert body["exit_code"] == 1 and "m.json" not in json.dumps(body)


def test_evidence_render_is_still_ocs_own(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (ws / "p.json").write_bytes(fx.read_fixture("evidence-pack.json"))
    assert main(["evidence", "render", "p.json", "--out", "v.html"]) == 0
    assert (ws / "v.html").is_file()
    assert main(["evidence"]) == 2
    assert "agep command" in capsys.readouterr().out


def test_layers_are_listed_in_help() -> None:
    from ocs.cli import build_parser

    text = build_parser().format_help()
    for layer in ("manifest", "replay", "evidence"):
        assert layer in text


# ---- real pinned components --------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
def test_real_manifest_replay_and_evidence_commands(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (ws / "m.json").write_bytes(fx.read_fixture("manifest.json"))
    (ws / "b.json").write_bytes(fx.read_fixture("replay-bundle.json"))
    (ws / "p.json").write_bytes(fx.read_fixture("evidence-pack.json"))
    assert main(["manifest", "validate", "m.json"]) == 0
    assert "VALID" in capsys.readouterr().out
    assert main(["manifest", "list-actions", "m.json"]) == 0
    assert main(["evidence", "summarize", "p.json"]) == 0
    assert "pack_id" in capsys.readouterr().out
    assert main(["replay", "sign", "b.json", "--secret", "k", "--out", "s.json"]) == 0
    assert main(["replay", "verify", "s.json", "--secret", "k"]) == 0
    capsys.readouterr()
    assert main(["replay", "verify", "s.json", "--secret", "wrong"]) == 1, (
        "the component's 1 is relayed"
    )


@pytest.mark.integration
@pytest.mark.usefixtures("stack")
def test_real_component_json_envelope_for_a_failed_verify(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (ws / "b.json").write_bytes(fx.read_fixture("replay-bundle.json"))
    assert main(["replay", "sign", "b.json", "--secret", "k", "--out", "s.json"]) == 0
    capsys.readouterr()
    assert main(["--format", "json", "replay", "verify", "s.json", "--secret", "wrong"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert (body["outcome"], body["reason_codes"]) == ("fail", ["OCS_BLOCKING_FINDING"])
    assert body["verdict_source"]["component"] == "replay-bundle"
