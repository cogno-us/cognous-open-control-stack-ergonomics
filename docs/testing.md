# Test defaults and locks

- **Network off.** `pytest` runs with `--disable-socket`; any socket use fails the test. The demo's acceptance job also runs under `unshare -n` in CI.
- **Coverage floor 80%** (`--cov-fail-under=80`, branch coverage on).
- **Warnings are errors** (`filterwarnings = ["error"]`) and unknown markers or config keys fail (`--strict-markers --strict-config`).
- **Strict typing:** `mypy` with `strict = true` over `src` and `tests`.
- **Lint:** `ruff check` and `ruff format --check`, including the `S` (security) rules.
- Integration tests (marker `integration`) run the real pinned components; CI sets `OCS_REQUIRE_STACK=1` so a missing stack fails instead of skipping.
- `tests/test_defaults.py` fails if one of these is loosened or a lock drifts from `ocs.lock`.

## Locks, one per extra

`requirements/dev.txt` and `requirements/stack.txt` are universal locks (Python 3.11 and up) with hashes for every PyPI package. The `stack` lock also records the four component git commits, the same ones as `ocs.lock`. Regenerate after changing `pyproject.toml`:

```
uv pip compile pyproject.toml --extra dev   --universal --python-version 3.11 --generate-hashes -o requirements/dev.txt
uv pip compile pyproject.toml --extra stack --universal --python-version 3.11 --generate-hashes -o requirements/stack.txt
```

`tests/test_distribution.py` builds the wheel and sdist, installs the wheel into a fresh venv with `--no-index` (the core package has no dependencies), and requires the `ocs` and `cognous-ocs` scripts to give identical output.

Install from a lock with `pip install -r requirements/stack.txt`. CI still installs with `pip install -e ".[dev,stack]"`; the locks are the reproducible record, not yet the CI install path.
