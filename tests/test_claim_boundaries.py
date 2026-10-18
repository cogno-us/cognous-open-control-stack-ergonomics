"""Claim-boundary checker: positive and negative cases, plus the repository's own state."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT, load_script

cb = load_script("check_claim_boundaries")

CERT = "certi" + "fied"
COMPL = "compl" + "iant"
PROD = "production" + "-ready"
EXCLUDE_SELF = {
    "path": "scripts/claim_boundaries_allowlist.json",
    "reason": "quotes the lines it excuses",
}
NB_HYPHEN = chr(0x2011)
SOFT_HYPHEN = chr(0xAD)
EN_DASH = chr(0x2013)


def phrase_rules(text: str) -> set[str]:
    return {f.rule for f in cb.scan_text("x.md", text)}


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (f"This build is {CERT}.", "certified"),
        (f"This build is {CERT.upper()}.", "certified"),
        ("We certify the output.", "certified"),
        ("Full certification achieved.", "certified"),
        (f"Fully {COMPL} with the policy.", "compliant"),
        ("Supports compliance reporting.", "compliant"),
        ("It complies with the standard.", "compliant"),
        (f"Status: {PROD}", "production-ready"),
        ("Status: production ready", "production-ready"),
        ("Status: production_ready", "production-ready"),
        ("Status: production\nready", "production-ready"),
        (f"Status: production{NB_HYPHEN}ready", "production-ready"),
        (f"Status: pro{SOFT_HYPHEN}duction-ready", "production-ready"),
        (f"Status: PRODUCTION{EN_DASH}READY", "production-ready"),
        ("Ready for production use", "production-ready"),
        ("An enterprise-grade tool", "production-ready"),
        ("audit-ready evidence", "production-ready"),
    ],
)
def test_positive_cases_are_found(text: str, rule: str) -> None:
    assert rule in phrase_rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "A certain ready state.",
        "It was certainly complicated.",
        "The production environment is ready when the queue drains.",
        "Readiness checks for production traffic",
        "reply and apply",
        "ocs relays component verdicts and decides nothing.",
        "Ready to use for a demo; the demo is not a release.",
    ],
)
def test_negative_cases_are_not_found(text: str) -> None:
    assert phrase_rules(text) == set()


def write_repo(
    root: Path,
    *,
    pub: object = False,
    prod: object = False,
    allow: list[dict[str, str]] | None = None,
) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "scripts" / "claim_boundaries_allowlist.json").write_text(
        json.dumps({"excluded_paths": [EXCLUDE_SELF], "allowed": allow or []})
    )
    state = {
        "schema": "ocs-release-state/1",
        "publication_authorized": pub,
        "production_authorized": prod,
        "basis": "b",
        "note": "n",
    }
    (root / "release-state.json").write_text(json.dumps(state))
    (root / "README.md").write_text("# Tool\n\nA plain statement.\n")


def test_clean_repo_passes_on_hold(tmp_path: Path) -> None:
    write_repo(tmp_path)
    code, msgs = cb.run(tmp_path, [])
    assert code == 0, msgs


def test_document_claim_fails_on_hold(tmp_path: Path) -> None:
    write_repo(tmp_path)
    (tmp_path / "docs" / "a.md").write_text(f"The stack is {PROD}.\n")
    code, msgs = cb.run(tmp_path, [])
    assert code == 1
    assert any("docs/a.md:1" in m for m in msgs)


def test_action_text_and_cli_string_are_scanned(tmp_path: Path) -> None:
    write_repo(tmp_path)
    action = tmp_path / ".github" / "actions" / "ocs"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(f"name: x\ndescription: 'a {CERT} run'\n")
    (tmp_path / "mod.py").write_text(f'MSG = "result is {COMPL}"\n')
    code, msgs = cb.run(tmp_path, [])
    assert code == 1
    joined = "\n".join(msgs)
    assert "action.yml" in joined
    assert "mod.py" in joined


def test_allow_list_excuses_only_the_exact_line(tmp_path: Path) -> None:
    line = f"The rule bans {CERT} claims."
    entry = {"path": "docs/a.md", "rule": "certified", "line": line, "reason": "states the rule"}
    write_repo(tmp_path, allow=[entry])
    (tmp_path / "docs" / "a.md").write_text(f"{line}\n")
    assert cb.run(tmp_path, [])[0] == 0
    (tmp_path / "docs" / "a.md").write_text(f"{line} Really {CERT}.\n")
    assert cb.run(tmp_path, [])[0] == 1


def test_stale_allow_list_entry_fails(tmp_path: Path) -> None:
    entry = {"path": "docs/a.md", "rule": "certified", "line": "gone", "reason": "old"}
    write_repo(tmp_path, allow=[entry])
    code, msgs = cb.run(tmp_path, [])
    assert code == 1
    assert any("stale allow-list entry" in m for m in msgs)


def test_allow_list_shape_is_closed(tmp_path: Path) -> None:
    write_repo(tmp_path)
    path = tmp_path / "scripts" / "claim_boundaries_allowlist.json"
    for bad in (
        {"excluded_paths": [], "allowed": [], "extra": 1},
        {
            "excluded_paths": [],
            "allowed": [{"path": "a", "rule": "certified", "line": "l", "reason": " "}],
        },
        {
            "excluded_paths": [],
            "allowed": [{"path": "a", "rule": "nope", "line": "l", "reason": "r"}],
        },
        {"excluded_paths": [{"path": "a", "reason": ""}], "allowed": []},
    ):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match=r"allow-list|entries|rule"):
            cb.load_allowlist(tmp_path)


def test_rules_are_not_enforced_only_when_both_authorized(tmp_path: Path) -> None:
    write_repo(tmp_path, pub=True, prod=True)
    (tmp_path / "docs" / "a.md").write_text(f"{PROD}\n")
    code, msgs = cb.run(tmp_path, [])
    assert code == 0
    assert "not enforced" in msgs[-1]
    write_repo_state(tmp_path, pub=True, prod=False)
    assert cb.run(tmp_path, [])[0] == 1


def write_repo_state(root: Path, *, pub: object, prod: object) -> None:
    state = json.loads((root / "release-state.json").read_text())
    state["publication_authorized"] = pub
    state["production_authorized"] = prod
    (root / "release-state.json").write_text(json.dumps(state))


@pytest.mark.parametrize("mode", ["missing", "bad-json", "string-bool", "extra-key"])
def test_missing_or_malformed_state_counts_as_hold(tmp_path: Path, mode: str) -> None:
    write_repo(tmp_path, pub=True, prod=True)
    (tmp_path / "docs" / "a.md").write_text(f"{PROD}\n")
    state = tmp_path / "release-state.json"
    if mode == "missing":
        state.unlink()
    elif mode == "bad-json":
        state.write_text("{")
    elif mode == "string-bool":
        write_repo_state(tmp_path, pub="true", prod="true")
    else:
        data = json.loads(state.read_text())
        data["extra"] = True
        state.write_text(json.dumps(data))
    code, msgs = cb.run(tmp_path, [])
    assert code == 1
    assert msgs[0].startswith("HOLD assumed")


def test_built_wheel_text_is_scanned(tmp_path: Path) -> None:
    write_repo(tmp_path)
    wheel = tmp_path / "ocs-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("ocs-0.dist-info/METADATA", f"Name: ocs\n\nIt is {COMPL}.\n")
        zf.writestr("ocs-0.dist-info/licenses/LICENSE", f"{COMPL}\n")
        zf.writestr("ocs/data.bin", b"\xff\xfe")
    code, msgs = cb.run(tmp_path, [wheel])
    assert code == 1
    assert any("README.md:1" in m for m in msgs)
    assert not any("LICENSE" in m for m in msgs)


def test_wheel_readme_uses_readme_allow_list_but_header_does_not(tmp_path: Path) -> None:
    line = f"The rule bans {CERT} claims."
    entry = {"path": "README.md", "rule": "certified", "line": line, "reason": "states the rule"}
    write_repo(tmp_path, allow=[entry])
    (tmp_path / "README.md").write_text(f"# T\n\n{line}\n")
    wheel = tmp_path / "ocs-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("ocs-0.dist-info/METADATA", f"Name: ocs\n\n# T\n\n{line}\n")
    assert cb.run(tmp_path, [wheel])[0] == 0
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("ocs-0.dist-info/METADATA", f"Name: ocs\nSummary: {line}\n\n# T\n\n{line}\n")
    code, msgs = cb.run(tmp_path, [wheel])
    assert code == 1
    assert any("#header" in m for m in msgs)


def test_undecodable_text_file_is_reported(tmp_path: Path) -> None:
    write_repo(tmp_path)
    (tmp_path / "docs" / "a.md").write_bytes(b"\xff\xfe\xfa")
    code, msgs = cb.run(tmp_path, [])
    assert code == 1
    assert any("undecodable" in m for m in msgs)


def test_this_repository_passes_its_own_check() -> None:
    code, msgs = cb.run(REPO_ROOT, [])
    assert code == 0, msgs


def test_this_repository_is_on_hold() -> None:
    state = json.loads((REPO_ROOT / "release-state.json").read_text())
    assert state["publication_authorized"] is False
    assert state["production_authorized"] is False


def test_cli_strings_are_free_of_claim_words(ws: Path) -> None:
    """Runtime output, not just source: version text, usage errors, an evidence render."""
    from ocs import cli, fixtures_data

    texts = [cli.version_text()]
    pack = ws / "pack.json"
    pack.write_bytes(fixtures_data.read_fixture("evidence-pack.json"))
    import contextlib
    import io

    for argv in (
        ["evidence", "render", str(pack), "--out", str(ws / "v.html"), "--format", "json"],
        ["nope"],
        [],
    ):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(argv)
        texts.append(buf.getvalue())
    texts.append((ws / "v.html").read_text(encoding="utf-8"))
    for text in texts:
        assert cb.scan_text("output", text) == []


def test_demo_output_is_free_of_claim_words(ws: Path, stack: None) -> None:
    import contextlib
    import io

    from ocs import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert (
            cli.main(["demo", "customer-service", "--out", str(ws / "d"), "--format", "json"]) == 0
        )
    assert cb.scan_text("demo", buf.getvalue()) == []
    assert cb.scan_text("demo.html", (ws / "d" / "evidence.html").read_text(encoding="utf-8")) == []
