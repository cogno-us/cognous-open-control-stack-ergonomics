"""csf_fd18fac96a9c68c8d48ec390: dependency integrity policy, one negative fixture per violation class.

The repository passes the checker; each mutation of a throwaway copy breaks exactly the rule it
names. Also: the CI install path and the composite action consume the committed locks, and the
lock-freshness gate notices a zeroed hash or an impossible version.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from conftest import REPO_ROOT, load_script

integ = load_script("check_dependency_integrity")
fresh = load_script("check_lock_freshness")

CI = ".github/workflows/ci.yml"
ACTION = ".github/actions/ocs/action.yml"
HASHED_INSTALL = "--require-hashes --no-deps --no-build-isolation"
VCS_INSTALL = "python -m pip install --no-deps --no-build-isolation -r requirements/stack-vcs.txt"


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """The files this policy reads, copied small and fast (the full copy costs seconds per test)."""
    dest = tmp_path / "repo"
    for rel in (".github", "requirements", "scripts"):
        shutil.copytree(
            REPO_ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__", ".*_cache")
        )
    for rel in (
        "Makefile",
        "pyproject.toml",
        "src/cognous_ocs/__init__.py",
        "src/cognous_ocs/lock.py",
    ):
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, dest / rel)
    (dest / "tests").mkdir()
    return dest


def edit(root: Path, rel: str, old: str, new: str, count: int = 1) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def append(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def write_new(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


def errors_of(root: Path) -> list[str]:
    found: list[str] = integ.check(root)
    return found


def expect(root: Path, needle: str) -> None:
    errors = errors_of(root)
    assert any(needle in e for e in errors), (needle, errors)


def test_repository_passes() -> None:
    assert integ.check(REPO_ROOT) == []


def test_cli_exit_codes(repo_copy: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert integ.main(["--root", str(repo_copy)]) == 0
    assert "dependency integrity: ok" in capsys.readouterr().out
    append(repo_copy, "Makefile", "\tpip install requests\n")
    assert integ.main(["--root", str(repo_copy)]) == 1


# ---- install commands: workflows -------------------------------------------------------------

BAD_WORKFLOW_COMMANDS = [
    ('python -m pip install -e ".[dev,stack]"', "must be a local path or wheel without extras"),
    ("python -m pip install requests", "must be a local path"),
    ("python -m pip install requests==2.0", "must be a local path"),
    (
        "python -m pip install git+https://github.com/cogno-us/cognous-control-plane@main",
        "must be a local path",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} --extra-index-url https://x.invalid/simple -r requirements/dev.txt",
        "--extra-index-url",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} --index-url https://x.invalid/simple -r requirements/dev.txt",
        "--index-url",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} -i https://x.invalid/simple -r requirements/dev.txt",
        "-i",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} --trusted-host x.invalid -r requirements/dev.txt",
        "--trusted-host",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} --find-links https://x.invalid -r requirements/dev.txt",
        "--find-links",
    ),
    (f"python -m pip install {HASHED_INSTALL} -U -r requirements/dev.txt", "-U"),
    (f"python -m pip install {HASHED_INSTALL} --pre -r requirements/dev.txt", "--pre"),
    (
        f"python -m pip install {HASHED_INSTALL} -c constraints.txt -r requirements/dev.txt",
        "constraints",
    ),
    (
        "python -m pip install --no-deps --no-build-isolation -r requirements/dev.txt",
        "lacks --require-hashes",
    ),
    (
        "python -m pip install --require-hashes --no-build-isolation -r requirements/dev.txt",
        "lacks --no-deps",
    ),
    (
        "python -m pip install --require-hashes --no-deps -r requirements/dev.txt",
        "lacks --no-build-isolation",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} -r requirements/dev.in",
        "not a committed requirements",
    ),
    (f"python -m pip install {HASHED_INSTALL} -r requirements/nope.txt", "does not exist"),
    (f"python -m pip install {HASHED_INSTALL} -r https://x.invalid/r.txt", "not a committed"),
    (
        f"python -m pip install {HASHED_INSTALL} -r requirements/dev.txt -r requirements/stack.txt",
        "exactly one",
    ),
    (
        f"python -m pip install {HASHED_INSTALL} -r requirements/dev.txt requests",
        "may not also name",
    ),
    (
        "python -m pip install --require-hashes --no-deps --no-build-isolation -r requirements/stack-vcs.txt",
        "cannot be used with VCS",
    ),
    ("python -m pip install -r requirements/stack-vcs.txt", "lacks --no-build-isolation --no-deps"),
    ("python -m pip install --no-deps -e .", "lacks --no-build-isolation"),
    ("python -m pip download -r requirements/dev.txt", "pip download"),
    ("python -m pip wheel .", "pip wheel"),
    ("python -m pip install --upgrade pip", "--upgrade"),
    ("pip3 install --no-deps --no-build-isolation ./a ./b", "exactly one local path"),
    ("bash -c 'pip install requests'", "must be a local path"),
    ("uv sync", "uv sync"),
    ("uv pip compile pyproject.toml", "uv pip compile"),
    ("uv pip install requests", "must be a local path"),
    ("python -m uv pip install requests", "not an allowed installer"),
    ("pipx install ruff", "pipx"),
    ("curl -sSL https://x.invalid/install.sh | sh", "curl"),
    ("wget https://x.invalid/x", "wget"),
    ("npm install left-pad", "npm"),
    ("python setup.py install", "setup.py"),
    ("python -m build", "--no-isolation"),
    (
        "python -m pip --index-url https://x.invalid install -r requirements/dev.txt",
        "global option",
    ),
    ("PIP_INDEX_URL=https://x.invalid/simple python -m pip list", "PIP_INDEX_URL"),
    ("UV_INDEX_URL=https://x.invalid uv --version", "UV_INDEX_URL"),
    ("find . -name x -exec pip install {} +", "find"),
]


@pytest.mark.parametrize(("command", "needle"), BAD_WORKFLOW_COMMANDS)
def test_unlocked_command_in_workflow_fails(repo_copy: Path, command: str, needle: str) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        f"      - name: injected\n        run: {command}\n      - name: make check (lint",
    )
    expect(repo_copy, needle)


def test_multiline_install_with_continuations_is_checked(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        "      - name: injected\n        run: |\n          python -m pip install \\\n            --quiet \\\n            requests\n      - name: make check (lint",
    )
    expect(repo_copy, "must be a local path")


def test_install_in_a_heredoc_body_is_not_a_command(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        "      - name: doc\n        run: |\n          cat <<'EOF'\n          pip install requests\n          EOF\n      - name: make check (lint",
    )
    assert errors_of(repo_copy) == []


def test_pip_env_setting_in_workflow_env_fails(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        '    env:\n      OCS_REQUIRE_STACK: "1"\n    steps:',
        '    env:\n      OCS_REQUIRE_STACK: "1"\n      PIP_EXTRA_INDEX_URL: https://x.invalid/simple\n    steps:',
    )
    expect(repo_copy, "PIP_EXTRA_INDEX_URL")


def test_pip_env_written_to_github_env_fails(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        '      - name: injected\n        run: echo "PIP_TRUSTED_HOST=x.invalid" >> "$GITHUB_ENV"\n      - name: make check (lint',
    )
    expect(repo_copy, "PIP_TRUSTED_HOST")


def test_harmless_pip_env_is_allowed(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        '    env:\n      OCS_REQUIRE_STACK: "1"\n    steps:',
        '    env:\n      OCS_REQUIRE_STACK: "1"\n      PIP_DISABLE_PIP_VERSION_CHECK: "1"\n    steps:',
    )
    assert errors_of(repo_copy) == []


def test_vcs_install_before_the_locked_toolchain_fails(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        f"      - name: injected\n        run: {VCS_INSTALL}\n      - name: make check (lint",
    )
    # The injected step is after the installs in the checks job, so move it to a job with none.
    assert errors_of(repo_copy) == []
    edit(
        repo_copy,
        CI,
        "  action-sample:\n",
        f"  extra:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: {VCS_INSTALL}\n\n  action-sample:\n",
    )
    expect(repo_copy, "needs the locked build toolchain")


def test_unparseable_command_mentioning_pip_fails(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        '      - name: injected\n        run: python -m pip install "unterminated\n      - name: make check (lint',
    )
    expect(repo_copy, "cannot parse")


# ---- install commands: composite action ------------------------------------------------------


def test_action_free_resolution_of_the_extra_fails(repo_copy: Path) -> None:
    """The original defect: the action ran `pip install "$root[stack]"` outside the locks."""
    action = (repo_copy / ACTION).read_text(encoding="utf-8")
    start = action.index('        venv="$RUNNER_TEMP/cognous-ocs-action-venv"')
    end = action.index('        echo "OCS_ACTION_BIN=')
    original = action[start:end]
    floating = '        python3 -m venv "$RUNNER_TEMP/cognous-ocs-action-venv"\n        "$RUNNER_TEMP/cognous-ocs-action-venv/bin/python" -m pip install --quiet "$root[stack]"\n'
    (repo_copy / ACTION).write_text(action.replace(original, floating), encoding="utf-8")
    expect(repo_copy, "$root[stack]")


def test_action_install_without_the_vcs_pin_step_still_passes_but_without_hash_flag_fails(
    repo_copy: Path,
) -> None:
    edit(
        repo_copy,
        ACTION,
        '--quiet --require-hashes --no-deps --no-build-isolation -r "$root/requirements/stack.txt"',
        '--quiet --no-deps --no-build-isolation -r "$root/requirements/stack.txt"',
    )
    expect(repo_copy, "lacks --require-hashes")


def test_action_install_of_the_checkout_must_not_resolve_dependencies(repo_copy: Path) -> None:
    edit(
        repo_copy,
        ACTION,
        '--quiet --no-deps --no-build-isolation "$root"',
        '--quiet --no-build-isolation "$root"',
    )
    expect(repo_copy, "lacks --no-deps")


def test_floating_action_ref_and_docker_image_fail(repo_copy: Path) -> None:
    edit(
        repo_copy,
        ACTION,
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f",
        "actions/upload-artifact@v6",
    )
    expect(repo_copy, "`uses: actions/upload-artifact@v6` is not pinned")


@pytest.mark.parametrize(
    "ref",
    [
        "actions/checkout@main",
        "actions/checkout@v6.0.2",
        "actions/checkout@de0fac2",
        "actions/checkout",
        "actions/checkout@DE0FAC2E4500DABE0009E67214FF5F5447CE83DD",
    ],
)
def test_non_sha_action_refs_fail(repo_copy: Path, ref: str) -> None:
    edit(repo_copy, CI, "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd", ref)
    expect(repo_copy, "is not pinned by a 40-hex commit SHA")


def test_docker_uses_and_container_images_need_digests(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        '      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2\n      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0\n        with:\n          python-version: "3.12"\n      - id: strict',
        '      - uses: docker://alpine:3.20\n      - uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0\n        with:\n          python-version: "3.12"\n      - id: strict',
    )
    expect(repo_copy, "floating docker image")
    edit(
        repo_copy,
        CI,
        "  action-sample:\n    # The composite",
        "  action-sample:\n    container: python:3.12\n    # The composite",
    )
    expect(repo_copy, "container image 'python:3.12' has no @sha256 digest")


def test_digest_pinned_docker_is_accepted(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "  action-sample:\n    # The composite",
        f"  action-sample:\n    container: python:3.12@sha256:{'a' * 64}\n    # The composite",
    )
    assert errors_of(repo_copy) == []


# ---- install commands: Makefile and scripts ---------------------------------------------------


def test_unlocked_makefile_install_fails(repo_copy: Path) -> None:
    append(repo_copy, "Makefile", '\nbad:\n\t$(PY) -m pip install ".[dev]"\n')
    expect(repo_copy, "Makefile:")


def test_make_build_with_isolation_fails(repo_copy: Path) -> None:
    edit(repo_copy, "Makefile", "-m build --no-isolation", "-m build")
    expect(repo_copy, "--no-isolation")


@pytest.mark.parametrize(
    "argv",
    [
        '["pip", "install", "requests"]',
        '[sys.executable, "-m", "pip", "install", "-q", "x[stack]"]',
        '[sys.executable, "-m", "pip", "install", "--extra-index-url", "https://x.invalid", "-r", "requirements/dev.txt"]',
        '["uv", "sync"]',
    ],
)
def test_unlocked_install_in_a_script_fails(repo_copy: Path, argv: str) -> None:
    write_new(
        repo_copy,
        "scripts/_extra.py",
        f"import subprocess, sys\nsubprocess.run({argv}, check=False)\n",
    )
    expect(repo_copy, "scripts/_extra.py:")


def test_unlocked_install_in_a_test_fails_unless_offline(repo_copy: Path) -> None:
    write_new(
        repo_copy, "tests/test_extra.py", 'X = ["python", "-m", "pip", "install", "requests"]\n'
    )
    expect(repo_copy, "tests/test_extra.py:")
    (repo_copy / "tests" / "test_extra.py").write_text(
        'X = ["python", "-m", "pip", "install", "--no-index", "./w.whl"]\n', encoding="utf-8"
    )
    assert errors_of(repo_copy) == []


def test_smoke_script_installs_only_from_locks() -> None:
    text = (REPO_ROOT / "scripts" / "smoke_install_wheel.py").read_text(encoding="utf-8")
    assert "[stack]" not in text
    for lock in ("build", "stack", "stack-vcs"):
        assert f'"requirements/{lock}.txt"' in text


# ---- the CI install path consumes the committed locks -----------------------------------------


def _install_lines(rel: str) -> list[str]:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8").replace("\\\n", " ")
    return [ln.strip() for ln in text.splitlines() if re.search(r"\bpip3? install\b", ln)]


def test_ci_workflow_installs_only_from_locks() -> None:
    lines = _install_lines(CI)
    assert lines
    for line in lines:
        assert "[" not in line.replace("[stack]", "[") or "[dev" not in line, line
        assert not re.search(r"\.\[", line), line
        assert "-r requirements/" in line or (
            "--no-deps" in line and "--no-build-isolation" in line
        ), line
    text = (REPO_ROOT / CI).read_text(encoding="utf-8")
    for lock in ("dev", "stack", "stack-vcs", "build"):
        assert f"-r requirements/{lock}.txt" in text, lock
    assert (
        text.count("--require-hashes --no-deps --no-build-isolation -r requirements/dev.txt") == 3
    )


def test_action_installs_exactly_the_committed_locks_in_order() -> None:
    lines = _install_lines(ACTION)
    assert re.findall(r"requirements/([\w-]+)\.txt", " ".join(lines[:3])) == [
        "build",
        "stack",
        "stack-vcs",
    ]
    assert (
        lines[3].endswith('"$root"')
        and "--no-deps" in lines[3]
        and "--no-build-isolation" in lines[3]
    )
    assert not any("[stack]" in ln for ln in lines)
    assert len(lines) == 4
    for ln in lines[:2]:
        assert "--require-hashes" in ln


# ---- locks -------------------------------------------------------------------------------------


def lock_path(root: Path, name: str) -> Path:
    return root / "requirements" / name


def test_zeroed_hashes_fail(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "dev.txt")
    path.write_text(
        re.sub(
            r"--hash=sha256:[0-9a-f]{64}",
            "--hash=sha256:" + "0" * 64,
            path.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    expect(repo_copy, "placeholder hash")


def test_missing_hash_fails(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "stack.txt")
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(annotated-types==[^\n]*)(?: \\\n    --hash=[^\n]*)+", r"\1", text, count=1)
    path.write_text(text, encoding="utf-8")
    expect(repo_copy, "annotated-types==0.8.0 has no sha256 hash")


def test_malformed_hash_fails(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "dev.txt")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("--hash=sha256:", "--hash=md5:", 1), encoding="utf-8")
    expect(repo_copy, "not a strict `name==version` pin")


def test_version_outside_pyproject_range_fails(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "dev.txt")
    path.write_text(
        re.sub(r"(?m)^pytest==[0-9.]+", "pytest==0.0.1", path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    expect(repo_copy, "pytest==0.0.1 violates 'pytest>=8'")


def test_unpinned_or_url_requirement_in_a_hashed_lock_fails(repo_copy: Path) -> None:
    append(repo_copy, "requirements/dev.txt", "requests>=2\n")
    append(repo_copy, "requirements/dev.txt", "evil @ https://x.invalid/evil.whl\n")
    append(repo_copy, "requirements/dev.txt", "--index-url https://x.invalid/simple\n")
    errors = errors_of(repo_copy)
    assert sum("is not a strict `name==version` pin" in e for e in errors) == 3


def test_duplicate_pin_and_shared_hash_fail(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "build.txt")
    text = path.read_text(encoding="utf-8")
    digest = "a" * 63 + "b"
    path.write_text(text + f"wheel==0.48.0 \\\n    --hash=sha256:{digest}\n", encoding="utf-8")
    expect(repo_copy, "wheel is pinned twice")


def test_lock_missing_a_dependency_fails(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "dev.txt")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"(?m)^pytest-socket==[^\n]*(?:\n    [^\n]*)*\n", "", text), encoding="utf-8"
    )
    expect(repo_copy, "does not lock pytest-socket")


def test_build_in_may_not_carry_urls_or_options(repo_copy: Path) -> None:
    append(
        repo_copy,
        "requirements/build.in",
        "evil @ https://x.invalid/e.whl\n--index-url https://x.invalid\n",
    )
    errors = errors_of(repo_copy)
    assert sum("only plain requirement names" in e for e in errors) == 2


# ---- first-party VCS pins -----------------------------------------------------------------------

AAM_SHA = "fa170815ef96a54c233d0dd9da821b60debabf8b"


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        (AAM_SHA, AAM_SHA[:12], "full 40-hex"),
        (f"@{AAM_SHA}", "@main", "full 40-hex"),
        (f"@{AAM_SHA}", f"@{AAM_SHA.upper()}", "full 40-hex"),
        (f"@{AAM_SHA}", f"@{'0' * 40}", "differs from cognous_ocs.lock"),
        (
            "git+https://github.com/cogno-us/cognous-action-manifest",
            "git+http://github.com/cogno-us/cognous-action-manifest",
            "full 40-hex",
        ),
        (
            "git+https://github.com/cogno-us/cognous-action-manifest",
            "git+https://github.com/someone/evil",
            "not in the allow-list",
        ),
        (
            "git+https://github.com/cogno-us/cognous-action-manifest",
            "git+ssh://git@github.com/cogno-us/cognous-action-manifest",
            "full 40-hex",
        ),
        (
            "git+https://github.com/cogno-us/cognous-action-manifest",
            "git+https://github.com/cogno-us/cognous-control-plane",
            "differs from cognous_ocs.lock",
        ),
    ],
)
def test_bad_vcs_pin_fails(repo_copy: Path, old: str, new: str, needle: str) -> None:
    edit(repo_copy, "requirements/stack-vcs.txt", old, new)
    expect(repo_copy, needle)


def test_dropped_or_extra_vcs_pin_fails(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "stack-vcs.txt")
    keep = [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.startswith("agent-replay-bundle")
    ]
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    expect(repo_copy, "pin for agent-replay-bundle differs")
    append(
        repo_copy,
        "requirements/stack-vcs.txt",
        f"extra @ git+https://github.com/cogno-us/other@{'a' * 40}\n",
    )
    expect(repo_copy, "not in the allow-list")


def test_vcs_url_in_pyproject_must_be_full_sha_and_match(repo_copy: Path) -> None:
    edit(
        repo_copy,
        "pyproject.toml",
        f"cognous-action-manifest@{AAM_SHA}",
        "cognous-action-manifest@main",
    )
    expect(repo_copy, "pyproject.toml extra stack")
    expect(repo_copy, "differs from the pyproject 'stack' extra")


def test_vcs_ref_in_any_scanned_file_must_be_a_full_sha(repo_copy: Path) -> None:
    edit(
        repo_copy,
        CI,
        "      - name: make check (lint",
        "      - name: injected\n        run: echo git+https://github.com/cogno-us/cognous-control-plane@main\n      - name: make check (lint",
    )
    expect(repo_copy, "ci.yml:")


# ---- build backend ------------------------------------------------------------------------------


def test_build_toolchain_must_be_locked(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "build.txt")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"(?m)^editables==[^\n]*(?:\n    [^\n]*)*\n", "", text), encoding="utf-8"
    )
    expect(repo_copy, "requirements/build.txt does not pin editables")


def test_build_and_dev_locks_must_agree_on_the_toolchain(repo_copy: Path) -> None:
    edit(repo_copy, "requirements/dev.txt", "hatchling==1.32.4", "hatchling==1.30.0")
    expect(repo_copy, "disagree on hatchling")


def test_unpinned_build_backend_range_must_be_satisfied_by_the_lock(repo_copy: Path) -> None:
    edit(
        repo_copy,
        "pyproject.toml",
        'requires = ["hatchling>=1.25"]',
        'requires = ["hatchling>=99"]',
    )
    expect(repo_copy, "requirements/build.txt: hatchling==1.32.4 violates 'hatchling>=99'")


def test_unlocked_extra_backend_fails(repo_copy: Path) -> None:
    edit(
        repo_copy,
        "pyproject.toml",
        'requires = ["hatchling>=1.25"]',
        'requires = ["hatchling>=1.25", "flit-core>=3"]',
    )
    expect(repo_copy, "does not lock flit-core")


# ---- alternate indexes --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["pip.conf", "uv.toml", ".pypirc", "pip.ini"])
def test_index_configuration_files_fail(repo_copy: Path, name: str) -> None:
    (repo_copy / name).write_text(
        "[global]\nindex-url = https://x.invalid/simple\n", encoding="utf-8"
    )
    expect(repo_copy, f"{name}: alternate package index")


@pytest.mark.parametrize("table", ["uv", "pip"])
def test_pyproject_tool_resolver_tables_fail(repo_copy: Path, table: str) -> None:
    append(
        repo_copy, "pyproject.toml", f'\n[tool.{table}]\nindex-url = "https://x.invalid/simple"\n'
    )
    expect(repo_copy, f"[tool.{table}]")


# ---- lock freshness now checks hashes and versions, not just names ----------------------------


def test_freshness_gate_detects_zeroed_hashes_and_impossible_versions(repo_copy: Path) -> None:
    """Reproducer for csf_fd18fac96a9c68c8d48ec390: at the base this gate printed `ok`."""
    assert fresh.check(repo_copy) == []
    for name in ("dev.txt", "stack.txt"):
        path = lock_path(repo_copy, name)
        text = re.sub(
            r"--hash=sha256:[0-9a-f]{64}",
            "--hash=sha256:" + "0" * 64,
            path.read_text(encoding="utf-8"),
        )
        path.write_text(re.sub(r"(?m)^pytest==[0-9.]+", "pytest==0.0.1", text), encoding="utf-8")
    errors = fresh.check(repo_copy)
    assert any("placeholder hash" in e for e in errors), errors
    assert any("pytest==0.0.1 violates" in e for e in errors), errors


def test_freshness_gate_detects_a_pin_without_hashes(repo_copy: Path) -> None:
    path = lock_path(repo_copy, "build.txt")
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"(wheel==[^\n]*)(?: \\\n    --hash=[^\n]*)+", r"\1", text, count=1),
        encoding="utf-8",
    )
    assert any("wheel==0.48.0 has no sha256 hash" in e for e in fresh.check(repo_copy))


def test_make_lock_runs_both_gates() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = text.split("\nlock:\n", 1)[1].split("\n\n", 1)[0]
    assert "check_lock_freshness.py" in recipe and "check_dependency_integrity.py" in recipe


# ---- unit tests of the specifier arithmetic -----------------------------------------------------


@pytest.mark.parametrize(
    ("version", "spec", "ok"),
    [
        ("8.0", ">=8", True),
        ("8", ">=8.0", True),
        ("7.9.9", ">=8", False),
        ("2026.9.21.13", ">=2026.9", True),
        ("1.2", "==1.2.0", True),
        ("1.2", "<1.2", False),
        ("1.2", ">=1,<2", True),
        ("3", "!=3", False),
    ],
)
def test_specifier_arithmetic(version: str, spec: str, ok: bool) -> None:
    lockfile = load_script("_lockfile")
    assert lockfile.satisfies(version, spec) is ok


def test_unsupported_specifier_is_not_silently_accepted() -> None:
    lockfile = load_script("_lockfile")
    assert lockfile.satisfies("1.0", "~=1.0") is None


# ---- bypass attempts found in independent review (shell syntax variants) -----------------------


def inject_script(root: Path, script: str) -> None:
    body = "".join(f"          {line}\n" for line in script.split("\n"))
    edit(
        root,
        CI,
        "      - name: make check (lint",
        f"      - name: injected\n        run: |\n{body}      - name: make check (lint",
    )


BYPASS_SCRIPTS = [
    ("uvx ruff check .", "uvx"),
    ("uv tool run ruff", "uv tool"),
    ("pip install --no-deps --no-build-isolation $PKG", "local path"),
    ('pip install --no-deps --no-build-isolation "$PKG"', "local path"),
    ("pip install --no-deps --no-build-isolation ${{ inputs.pkg }}", "local path"),
    ('pip install --no-deps --no-build-isolation "$OCS_IN_PKG"', "local path"),
    ("bash <<EOF\npip install requests\nEOF", "local path"),
    ("sh -s <<'EOF'\npip install requests\nEOF", "local path"),
    (
        "python - <<EOF\nimport subprocess\nsubprocess.run(['pip', 'install', 'requests'])\nEOF",
        "local path",
    ),
    ('eval "pip install requests"', "local path"),
    ("x=`pip install requests`", "local path"),
    ('x="$(pip install requests)"', "local path"),
    (
        "python -c \"import subprocess; subprocess.run(['pip','install','requests'])\"",
        "local path",
    ),
    ("python -c \"import pip; pip.main(['install', 'requests'])\"", "pip internals"),
    ("python -c \"import os; os.system('pip install requests')\"", "local path"),
    ("python3 -u -m pip install requests", "local path"),
    ("python -W ignore -m pip install requests", "local path"),
    ("python3 -I -m pip install requests", "local path"),
    ("$PY -m pip install requests", "local path"),
    ('"$PYTHON" -m pip install requests', "local path"),
    ("timeout 60 pip install requests", "local path"),
    ("nice -n 5 pip install requests", "local path"),
    ("sudo -E pip install requests", "local path"),
    ("env -i pip install requests", "local path"),
    ("if true; then pip install requests; fi", "local path"),
    ("if true; then true; else pip install requests; fi", "local path"),
    ('while read p; do pip install "$p"; done < f', "local path"),
    ("f() { pip install requests; }; f", "local path"),
    ("{ pip install requests; }", "local path"),
    ('INSTALL="pip install requests"\n$INSTALL', "local path"),
    ("hatch build", "hatch"),
    ("tox", "tox"),
]


@pytest.mark.parametrize(("script", "needle"), BYPASS_SCRIPTS)
def test_shell_syntax_variants_cannot_hide_an_unlocked_install(
    repo_copy: Path, script: str, needle: str
) -> None:
    inject_script(repo_copy, script)
    expect(repo_copy, needle)


@pytest.mark.parametrize(
    "script",
    [
        "cat <<EOF\npip install requests\nEOF",
        "echo pip install requests",
        "timeout 60 make check",
        "python3 -u -c 'print(1)'",
        "if true; then echo ok; fi",
    ],
)
def test_data_heredocs_and_harmless_commands_stay_allowed(repo_copy: Path, script: str) -> None:
    inject_script(repo_copy, script)
    assert errors_of(repo_copy) == []


def test_only_the_checkout_variables_are_known_local_targets(repo_copy: Path) -> None:
    preamble = (
        "pip install --require-hashes --no-deps --no-build-isolation -r requirements/build.txt\n"
    )
    for target in ('"$root"', "$RUNNER_TEMP/other-ocs", "${GITHUB_WORKSPACE}", "."):
        inject_script(repo_copy, preamble + f"pip install --no-deps --no-build-isolation {target}")
        assert errors_of(repo_copy) == [], target
        edit(repo_copy, CI, "      - name: injected\n", "      - name: done\n", 1)
        (repo_copy / CI).write_text(
            re.sub(
                r"      - name: done\n        run: \|\n(?:          .*\n)+",
                "",
                (repo_copy / CI).read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )


def test_makefile_variable_installer_and_env_lines_fail(repo_copy: Path) -> None:
    append(repo_copy, "Makefile", "\nPIP = pip\n")
    expect(repo_copy, "make variable that names an installer")
    append(repo_copy, "Makefile", "export PIP_INDEX_URL=https://x.invalid/simple\n")
    expect(repo_copy, "PIP_INDEX_URL")


def test_shell_files_dockerfiles_and_local_actions_are_scanned(repo_copy: Path) -> None:
    (repo_copy / "tools").mkdir(exist_ok=True)
    write_new(repo_copy, "tools/setup.sh", "#!/bin/sh\npip install requests\n")
    expect(repo_copy, "tools/setup.sh")
    write_new(repo_copy, "Dockerfile", "FROM python:3.12\nRUN pip install requests\n")
    expect(repo_copy, "FROM python:3.12 has no @sha256 digest")
    expect(repo_copy, "Dockerfile:2")
    (repo_copy / "tools" / "act").mkdir(parents=True)
    write_new(
        repo_copy,
        "tools/act/action.yml",
        "name: a\ndescription: d\nruns:\n  using: composite\n  steps:\n"
        "    - shell: bash\n      run: pip install requests\n",
    )
    expect(repo_copy, "tools/act/action.yml")


def test_script_command_strings_are_scanned(repo_copy: Path) -> None:
    write_new(
        repo_copy,
        "scripts/_extra.py",
        'import os, subprocess\nos.system("pip install requests")\n'
        'subprocess.run("pip install x", shell=True, check=False)\n',
    )
    errors = errors_of(repo_copy)
    assert sum("scripts/_extra.py:" in e for e in errors) >= 2


def test_build_requires_direct_url_and_runtime_dependencies_fail(repo_copy: Path) -> None:
    edit(
        repo_copy,
        "pyproject.toml",
        'requires = ["hatchling>=1.25"]',
        'requires = ["hatchling @ https://x.invalid/h.whl"]',
    )
    expect(repo_copy, "is a direct reference")
    edit(repo_copy, "pyproject.toml", "dependencies = []", 'dependencies = ["requests"]')
    expect(repo_copy, "dependencies must stay empty")
    append(repo_copy, "pyproject.toml", "\n[tool.hatch.envs.default]\ndependencies = []\n")
    expect(repo_copy, "tool.hatch.envs")
