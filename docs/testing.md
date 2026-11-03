# Test defaults and locks

- **Network off.** `pytest` runs with `--disable-socket`; any socket use fails the test. The demo's acceptance job also runs under `unshare -n` in CI.
- **Coverage floor 80%** (`--cov-fail-under=80`, branch coverage on).
- **Warnings are errors** (`filterwarnings = ["error"]`) and unknown markers or config keys fail (`--strict-markers --strict-config`).
- **Strict typing:** `mypy` with `strict = true` over `src` and `tests`.
- **Lint:** `ruff check` and `ruff format --check`, including the `S` (security) rules.
- Integration tests (marker `integration`) run the real pinned components; CI sets `OCS_REQUIRE_STACK=1` so a missing stack fails instead of skipping.
- `tests/test_defaults.py` fails if one of these is loosened or a lock drifts from `cognous_ocs.lock`.

## Locks, one per extra

Everything CI, the composite action and `make install-dev` install comes from the committed files in `requirements/`; nothing is resolved from the pyproject ranges or the live index.

| File | Contents | Installed with |
|---|---|---|
| `dev.txt` | Hashed PyPI pins for the `dev` extra plus the build toolchain | `pip install --require-hashes --no-deps --no-build-isolation -r requirements/dev.txt` |
| `stack.txt` | Hashed PyPI pins for the `stack` extra's dependencies (not the four components) | same form with `stack.txt` |
| `build.txt` | The build toolchain alone: `hatchling`, `editables` (editable installs), `setuptools` and `wheel` (the declared backends of the four components) | same form with `build.txt`; the composite action and the smoke test use it because they do not install the dev lock |
| `stack-vcs.txt` | The four first-party components, `dist @ git+https://github.com/cogno-us/<repo>@<40-hex SHA>`, equal to `src/cognous_ocs/lock.py` | `pip install --no-deps --no-build-isolation -r requirements/stack-vcs.txt` |

Why the split: pip refuses `--require-hashes` for version-control requirements, and the decision for this sprint is that a full 40-hex commit SHA is sufficient integrity for first-party VCS dependencies (no wheel hashes). The PyPI part keeps `--require-hashes`; the VCS part is its own SHA-pinned `--no-deps` step. The project itself is installed last with `--no-deps --no-build-isolation` (`-e .` for development), so neither the build backend nor a dependency is resolved live.

`dev.txt`, `stack.txt` and `build.txt` are uv-generated universal locks (Python 3.11 and up). `stack-vcs.txt` and `build.in` are hand-maintained inputs.

`scripts/check_lock_freshness.py` (names, pins equal `cognous_ocs.lock`, hashes present and not placeholders, versions inside the pyproject ranges) and `scripts/check_dependency_integrity.py` (the install-command policy) both run in `make lock`, hence in `make check` and CI. The policy: every `pip install` / `uv pip install` in `.github/`, the Makefile, `scripts/`, `tools/` and the tests' argv lists must be one of the lock-consuming forms above (or an offline `--no-index` install of a local path); any other pip or uv command that resolves, an index / find-links / trusted-host / constraint option or `PIP_*`/`UV_*` setting, `-U`, `--pre`, extras such as `.[stack]`, `python -m build` without `--no-isolation`, `curl`/`wget`/`npm`, and `pip.conf`/`uv.toml`/`[tool.uv]` are refused. VCS references must be `git+https://github.com/<allow-listed repo>@<40 lowercase hex>` and equal the Cognous lock. Every GitHub Action `uses:` is a 40-hex SHA (or a local path, or a `docker://` image with a sha256 digest). Tests for each violation class are in `tests/test_dependency_integrity.py`.

Scope of the install-command check. Covered: every workflow and composite action under `.github/` and any `action.yml` elsewhere in the repository (`run:` scripts, including heredoc bodies fed to a shell or Python interpreter, `eval`, `bash -c`, `$(...)` and backticks, and shell keywords, wrappers such as `timeout`/`nice`/`sudo`/`env` and interpreter options such as `-u`/`-W x` in front of `pip`), Makefile recipes and variable lines, `*.sh` files, Dockerfile `RUN`/`FROM` lines, and in `scripts/`, `tools/` and `tests/` Python argv lists (plus command strings such as `os.system("pip ...")` in `scripts/` and `tools/`). `uvx`, `uv tool`/`run`/`sync`, `pipx`, `hatch`, `tox`, `nox`, `poetry`, `curl`, `wget` and `npm` are refused. A local install target must be `.`, a literal path, a wheel, or the checkout/runner variables (`$root`, `$GITHUB_WORKSPACE`, `$GITHUB_ACTION_PATH`, `$RUNNER_TEMP`); any other variable could be a package name and is refused. Not covered, by design: a command assembled at run time from several variables, installs performed by code that is downloaded or generated while CI runs, and prose in documentation. The check is a static policy gate that catches ordinary edits, not a sandbox.

### Updating the locks (local, deterministic)

A networked regeneration job is deliberately not part of CI. Regeneration is a reviewed local step with `uv` (`pip install uv` is not part of the locks; use any recent uv). Start from the committed lock files, which uv reads as preferences, so **regeneration never changes a version unless the change is a dependency-upgrade PR**:

```
uv pip compile pyproject.toml requirements/build.in --extra dev --universal --python-version 3.11 --generate-hashes -o requirements/dev.txt
uv pip compile requirements/build.in -c requirements/dev.txt --universal --python-version 3.11 --generate-hashes -o requirements/build.txt
uv pip compile pyproject.toml --extra stack --universal --python-version 3.11 --generate-hashes \
  --no-emit-package agent-action-manifest --no-emit-package agent-control-plane \
  --no-emit-package agent-governance-evidence-pack --no-emit-package agent-replay-bundle -o requirements/stack.txt
```

The order matters: `dev.txt` first, then `build.txt` constrained to it, so the two agree on the toolchain. Then:

1. Review `git diff requirements/`. For a non-upgrade change, the set of `name==version` lines may only gain or lose the packages you intended (compare with `git diff requirements/ | grep '^[+-][a-z]'`); no existing version moves.
2. Component repin: set the commit in `pyproject.toml` (the `stack` extra), `src/cognous_ocs/lock.py` and `requirements/stack-vcs.txt` together; `make lock` fails if one differs. Regenerate `stack.txt` if the component's dependencies changed.
3. Run `make lock`, then build a fresh virtualenv with `make install-dev` and run the full suite.

`tests/test_distribution.py` builds the wheel and sdist, installs the wheel into a fresh venv with `--no-index` (the core package has no dependencies), and requires `cognous-ocs` (and `python -m cognous_ocs`) to work, `ocs` not to be installed, and no top-level `ocs` module.

The locks are the CI install path (`make install-dev`, the CI install steps, the composite action's install step and the wheel smoke test).
