"""Docs alignment, examples parity, lock freshness, wheel contents, release authorization."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from conftest import REPO_ROOT, load_script

docs = load_script("check_docs_alignment")
parity = load_script("check_examples_parity")
lock = load_script("check_lock_freshness")
wheel = load_script("check_wheel_contents")
authz = load_script("check_release_authorization")

FIX = "src/cognous_ocs/fixtures/customer-service"


def test_repository_passes_all_static_checks() -> None:
    assert docs.run(REPO_ROOT) == []
    assert parity.check(REPO_ROOT) == []
    assert lock.check(REPO_ROOT) == []


# docs alignment


def test_broken_link_and_missing_anchor_are_found(repo_copy: Path) -> None:
    readme = repo_copy / "README.md"
    readme.write_text(
        readme.read_text() + "\n[gone](docs/nope.md) [bad](docs/demo.md#no-such-heading)\n"
    )
    errors = docs.run(repo_copy)
    assert any("broken link docs/nope.md" in e for e in errors)
    assert any("missing anchor docs/demo.md#no-such-heading" in e for e in errors)


def test_links_in_code_fences_and_external_links_are_ignored(repo_copy: Path) -> None:
    readme = repo_copy / "README.md"
    readme.write_text(
        readme.read_text()
        + "\n```\n[x](missing.md)\n```\n[ext](https://example.invalid/x) [m](mailto:a@b.c)\n"
    )
    assert docs.run(repo_copy) == []


def test_unlinked_doc_page_is_found(repo_copy: Path) -> None:
    (repo_copy / "docs" / "orphan.md").write_text("# Orphan\n")
    assert any("docs/orphan.md is not linked" in e for e in docs.run(repo_copy))


def test_unlisted_command_is_found(repo_copy: Path) -> None:
    contract = repo_copy / "docs" / "cli-contract.md"
    lines = [
        x for x in contract.read_text().splitlines() if not x.startswith("| `cognous-ocs demo ")
    ]
    contract.write_text("\n".join(lines) + "\n")
    assert any("registered command 'demo'" in e for e in docs.run(repo_copy))


def test_version_and_exit_code_drift_are_found(repo_copy: Path) -> None:
    contract = repo_copy / "docs" / "cli-contract.md"
    text = (
        contract.read_text()
        .replace("(draft 0.1.0)", "(draft 9.9.9)")
        .replace("| 3 | Not applicable", "| 7 | Not applicable")
    )
    contract.write_text(text)
    errors = docs.run(repo_copy)
    assert any("draft" in e for e in errors)
    assert any("exit codes" in e for e in errors)


# examples parity


def test_edited_fixture_byte_fails_parity(repo_copy: Path) -> None:
    target = repo_copy / FIX / "evidence-pack.json"
    target.write_bytes(target.read_bytes() + b" ")
    assert any("evidence-pack.json: bytes differ" in e for e in parity.check(repo_copy))


def test_missing_and_extra_fixture_files_fail_parity(repo_copy: Path) -> None:
    (repo_copy / FIX / "run-record.json").unlink()
    (repo_copy / FIX / "stray.json").write_text("{}")
    errors = parity.check(repo_copy)
    assert any("run-record.json: missing" in e for e in errors)
    assert any("fixture directory holds" in e for e in errors)


def test_source_dir_comparison(repo_copy: Path, tmp_path: Path) -> None:
    src = tmp_path / "hub"
    src.mkdir()
    (src / "manifest.json").write_bytes((repo_copy / FIX / "manifest.json").read_bytes())
    assert parity.check(repo_copy, src) == []
    (src / "manifest.json").write_bytes(b"{}")
    assert any("differs from the source checkout" in e for e in parity.check(repo_copy, src))


def test_broken_provenance_is_reported(repo_copy: Path) -> None:
    (repo_copy / FIX / "provenance.json").write_text("{")
    assert any("provenance.json unreadable" in e for e in parity.check(repo_copy))


def test_byte_equal_claim_must_match_recorded_source(repo_copy: Path) -> None:
    path = repo_copy / FIX / "provenance.json"
    prov = json.loads(path.read_text())
    prov["sources"]["manifest.json"]["sha256"] = "0" * 64
    path.write_text(json.dumps(prov))
    assert any("recorded as byte-equal" in e for e in parity.check(repo_copy))


# lock freshness


def test_stale_pin_fails_lock_freshness(repo_copy: Path) -> None:
    from cognous_ocs.lock import COMPONENTS

    sha = COMPONENTS["aam"].sha
    pyproject = repo_copy / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(sha, "0" * 40))
    assert any("agent-action-manifest differs" in e for e in lock.check(repo_copy))


def test_lock_file_must_carry_every_pin(repo_copy: Path) -> None:
    (repo_copy / "requirements.lock").write_text("nothing\n")
    assert len(lock.check(repo_copy)) == 4


# wheel contents


def make_wheel(path: Path, extra: dict[str, str] | None = None, drop: str | None = None) -> Path:
    members = {name: "x" for name in wheel.REQUIRED}
    members["cognous_ocs-0.dist-info/licenses/LICENSE"] = "x"
    members["cognous_ocs-0.dist-info/entry_points.txt"] = (
        "[console_scripts]\ncognous-ocs = cognous_ocs.cli:main\n"
    )
    members["cognous_ocs-0.dist-info/METADATA"] = (
        "Name: cognous-ocs\nProvides-Extra: stack\nRequires-Dist: agent-x; extra == 'stack'\n"
    )
    members["cognous_ocs/schema_resources/v1/envelope.schema.json"] = "{}"
    members.update(extra or {})
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            if name != drop:
                zf.writestr(name, body)
    return path


def test_good_wheel_passes(tmp_path: Path) -> None:
    assert wheel.check(make_wheel(tmp_path / "a.whl")) == []


def test_wheel_missing_required_and_with_forbidden_members_fails(tmp_path: Path) -> None:
    bad = make_wheel(
        tmp_path / "a.whl",
        extra={
            "tests/test_x.py": "x",
            "release-state.json": "{}",
            "cognous_ocs/__pycache__/a.pyc": "x",
        },
        drop="cognous_ocs/py.typed",
    )
    errors = wheel.check(bad)
    assert any("missing from wheel: cognous_ocs/py.typed" in e for e in errors)
    assert any("must not ship: tests/test_x.py" in e for e in errors)
    assert any("must not ship: release-state.json" in e for e in errors)
    assert any("bytecode" in e for e in errors)


def test_core_dependency_on_a_component_fails(tmp_path: Path) -> None:
    bad = make_wheel(
        tmp_path / "a.whl",
        extra={
            "cognous_ocs-0.dist-info/METADATA": (
                "Name: cognous-ocs\nRequires-Dist: agent-x\nProvides-Extra: stack\n"
            )
        },
    )
    assert any("optional extra" in e for e in wheel.check(bad))


def test_wheel_main_needs_exactly_one_wheel(tmp_path: Path) -> None:
    assert wheel.main([str(tmp_path)]) == 1
    make_wheel(tmp_path / "a.whl")
    assert wheel.main([str(tmp_path)]) == 0


# release authorization


def write_state(root: Path, pub: object, prod: object) -> None:
    state = {
        "schema": "ocs-release-state/1",
        "publication_authorized": pub,
        "production_authorized": prod,
        "basis": "b",
        "note": "n",
    }
    (root / "release-state.json").write_text(json.dumps(state))


def test_this_repository_is_not_authorized_to_publish() -> None:
    assert authz.main([]) == 1


@pytest.mark.parametrize(
    ("pub", "prod", "expected"),
    [(False, False, 1), (True, False, 1), (False, True, 1), (True, True, 0), ("true", True, 1)],
)
def test_authorization_gate(tmp_path: Path, pub: object, prod: object, expected: int) -> None:
    write_state(tmp_path, pub, prod)
    assert authz.main(["--root", str(tmp_path)]) == expected


def test_missing_state_is_refused(tmp_path: Path) -> None:
    assert authz.main(["--root", str(tmp_path)]) == 1


# Makefile structure


def test_makefile_order_and_no_publish_command() -> None:
    text = (REPO_ROOT / "Makefile").read_text()
    recipe = text.split("release-publish-check:\n", 1)[1].split("\n\n", 1)[0]
    assert (
        recipe.index("check_claim_boundaries")
        < recipe.index("release-check")
        < recipe.index("check_release_authorization")
    )
    lowered = text.lower()
    for banned in ("twine upload", "pypi.org", "gh release", "git tag", "git push", "uv publish"):
        assert banned not in lowered
    check_line = next(x for x in text.splitlines() if x.startswith("check:"))
    assert check_line.split()[1:] == [
        "lint",
        "type",
        "lock",
        "tests",
        "docs",
        "claims",
        "examples",
        "schemas",
        "actions",
        "build",
    ]


def test_wheel_without_the_cognous_ocs_script_fails(tmp_path: Path) -> None:
    bad = make_wheel(
        tmp_path / "a.whl",
        extra={
            "cognous_ocs-0.dist-info/entry_points.txt": "[console_scripts]\nocs = cognous_ocs.cli:main\n"
        },
    )
    assert any("`cognous-ocs = cognous_ocs.cli:main` missing" in e for e in wheel.check(bad))


def test_wheel_shipping_an_ocs_script_fails(tmp_path: Path) -> None:
    bad = make_wheel(
        tmp_path / "a.whl",
        extra={
            "cognous_ocs-0.dist-info/entry_points.txt": (
                "[console_scripts]\ncognous-ocs = cognous_ocs.cli:main\nocs = cognous_ocs.cli:main\n"
            )
        },
    )
    assert any("`ocs` console script must not ship" in e for e in wheel.check(bad))


def test_wheel_must_be_named_for_the_distribution(tmp_path: Path) -> None:
    bad = make_wheel(tmp_path / "a.whl")
    old = tmp_path / "old.whl"
    with zipfile.ZipFile(bad) as src, zipfile.ZipFile(old, "w") as dst:
        for name in src.namelist():
            dst.writestr(name.replace("cognous_ocs-0", "ocs-0"), src.read(name))
    assert any("not named for the distribution" in e for e in wheel.check(old))
