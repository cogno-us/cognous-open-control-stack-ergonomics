from __future__ import annotations

import os
from pathlib import Path

import pytest

from ocs.paths import PathRefused, check_input, confine_output, refuse_alias


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "ws"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def test_relative_output_inside_workspace(ws: Path) -> None:
    assert confine_output("out/x.html") == ws / "out" / "x.html"


def test_absolute_output_inside_temp(tmp_path: Path, ws: Path) -> None:
    target = tmp_path / "elsewhere" / "x.html"
    assert confine_output(str(target)) == target


def test_outside_roots_refused(ws: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        confine_output("/etc/ocs-test-out")
    with pytest.raises(PathRefused, match="outside"):
        confine_output("../../../../../../etc/x")


@pytest.mark.parametrize("raw", ["a\nb", "a\rb", "a\x00b"])
def test_control_characters_in_output_refused(ws: Path, raw: str) -> None:
    with pytest.raises(PathRefused, match="CR, LF or NUL"):
        confine_output(raw)


def test_root_itself_refused(ws: Path) -> None:
    with pytest.raises(PathRefused, match="root itself"):
        confine_output(".")


def test_symlink_component_refused(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathRefused, match="symlink"):
        confine_output("link/x.html")


def test_symlink_leaf_refused(ws: Path) -> None:
    (ws / "real.txt").write_text("x")
    (ws / "leaf").symlink_to(ws / "real.txt")
    with pytest.raises(PathRefused, match="symlink"):
        confine_output("leaf")


def test_hard_link_refused(ws: Path) -> None:
    (ws / "a.txt").write_text("x")
    os.link(ws / "a.txt", ws / "b.txt")
    with pytest.raises(PathRefused, match="hard link"):
        confine_output("b.txt")


def test_input_checks(ws: Path) -> None:
    (ws / "in.json").write_text("{}")
    assert check_input("in.json") == Path("in.json")
    with pytest.raises(PathRefused, match="CR, LF"):
        check_input("in.json\n")
    with pytest.raises(PathRefused, match="regular file"):
        check_input("missing.json")
    with pytest.raises(PathRefused, match="regular file"):
        check_input(".")


def test_runner_temp_is_an_allowed_root(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner"))
    assert confine_output(str(tmp_path / "runner" / "o.txt")).name == "o.txt"


def test_alias_between_input_and_output_refused(ws: Path) -> None:
    (ws / "in.json").write_text("{}")
    with pytest.raises(PathRefused, match="aliases"):
        refuse_alias([Path("in.json")], [ws / "in.json"])
    (ws / "other").write_text("x")
    refuse_alias([Path("in.json")], [ws / "other"])
