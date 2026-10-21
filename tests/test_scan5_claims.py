"""Scan 5 claim-scanner fixes: angle-bracket prose, small-capital lookalikes, "prod ready"."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, load_script

cb = load_script("check_claim_boundaries")

CERT = "certi" + "fied"
PROD = "production" + "-ready"


def rules(text: str) -> set[str]:
    return {f.rule for f in cb.scan_text("x.md", text)}


@pytest.mark.parametrize(
    "text",
    [
        f"When a<b the tool is {CERT} and {PROD}; when c>d it is not.",
        f"if n<m this is {CERT}; else k>j",
        f"x <y\nthe tool is {CERT}\nz> end",
        f"a <b> then {CERT} <i>",
    ],
)
def test_prose_between_angle_brackets_is_still_scanned(text: str) -> None:
    assert "certified" in rules(text)


def test_both_claims_in_the_reported_sentence_are_found() -> None:
    assert rules(f"When a<b the tool is {CERT} and {PROD}; when c>d it is not.") == {
        "certified",
        "production-ready",
    }


def test_the_reported_document_fails_the_checker(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root / "scripts").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "scripts" / "claim_boundaries_allowlist.json").write_text(
        '{"excluded_paths": [], "allowed": []}'
    )
    (root / "release-state.json").write_text(
        (REPO_ROOT / "release-state.json").read_text(encoding="utf-8")
    )
    (root / "docs" / "x.md").write_text(f"When a<b the tool is {CERT}; when c>d it is not.\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    code, msgs = cb.run(root, [])
    assert code == 1
    assert any("docs/x.md:1" in m for m in msgs)


@pytest.mark.parametrize(
    "text",
    [
        "certi<b></b>fied",
        "cert<em>ified</em>",
        "production<br/>-ready",
        "<span>certi</span><span>fied</span>",
        "certi<my-tag attr='1'>fied",
        "certi<!-- hidden -->fied",
        "certi**fied**",
    ],
)
def test_real_markup_still_cannot_split_a_word(text: str) -> None:
    assert rules(text) & {"certified", "production-ready"}


@pytest.mark.parametrize(
    "text",
    ["a < b and c > d", "x<y but y>z", "<b>bold</b> text", "plain <code>code</code> only"],
)
def test_ordinary_text_has_no_findings(text: str) -> None:
    assert rules(text) == set()


def test_unknown_tag_name_is_not_stripped_but_text_still_scanned() -> None:
    assert "certified" in rules(f"a<zz is {CERT} zz>b")


def test_the_repository_still_passes_with_no_new_matches() -> None:
    code, msgs = cb.run(REPO_ROOT, [])
    assert code == 0, msgs
    assert "6 excused matches" in msgs[-1]


@pytest.mark.parametrize(
    ("char", "letter"),
    [
        (0x1D04, "c"),
        (0xA730, "f"),
        (0x026A, "i"),
        (0x1D00, "a"),
        (0x0299, "b"),
        (0x0262, "g"),
        (0x029C, "h"),
        (0x1D0A, "j"),
        (0x1D0B, "k"),
        (0x029F, "l"),
        (0x1D0D, "m"),
        (0xA731, "s"),
        (0x028F, "y"),
    ],
)
def test_small_capital_letters_fold_to_latin(char: int, letter: str) -> None:
    assert chr(char).translate(cb.LOOKALIKES) == letter


def test_small_capital_spellings_are_flagged() -> None:
    small = {"c": chr(0x1D04), "i": chr(0x026A), "f": chr(0xA730)}
    word = "".join(small.get(c, c) for c in CERT)
    assert word != CERT
    assert "certified" in rules(f"This build is {word}.")
    mixed = "".join(small.get(c, c) for c in "compliant")
    assert "compliant" in rules(f"Fully {mixed}.")


@pytest.mark.parametrize(
    "text", ["Ready for prod", "ready-for-prod", "prod ready", "Prod-Ready", "ready_for_prod."]
)
def test_prod_shorthand_is_flagged(text: str) -> None:
    assert "production-ready" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "ready for product launch",
        "ready for prodding",
        "ready for the prodigy",
        "the prod environment is separate",
        "a reproducible build",
    ],
)
def test_prod_shorthand_has_no_false_positives(text: str) -> None:
    assert "production-ready" not in rules(text)


def test_tag_pattern_does_not_backtrack_on_long_hyphen_runs() -> None:
    import time

    start = time.perf_counter()
    cb.scan_text("x.md", "<A" + "-" * 5000 + "!")
    assert time.perf_counter() - start < 2
