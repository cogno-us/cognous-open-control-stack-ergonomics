# Contributor gates. Activate a virtualenv and run `make install-dev` first (installs from the
# committed locks only; see docs/development.md).
# Nothing here publishes anything: there is no upload target, and release-publish-check
# fails while release-state.json says publication is not authorized.
PY ?= python
DIST ?= dist

.PHONY: check install-dev lint type lock tests docs claims examples schemas actions build release-check wheel-check smoke \
        release-publish-check pre-push clean

# Fixed order: lint, type, lock freshness, tests (network off, coverage floor), docs alignment,
# claim boundary, examples parity, schema snapshots, action hygiene, build.
check: lint type lock tests docs claims examples schemas actions build

lint:
	ruff check .
	ruff format --check .

type:
	mypy

# Installs ONLY from the committed locks: hashed PyPI pins, then the first-party components by
# full commit SHA (pip cannot hash VCS requirements), then this checkout with --no-deps. The
# toolchain (hatchling, editables, setuptools, wheel) is locked and nothing is built in isolation.
install-dev:
	$(PY) -m pip install --require-hashes --no-deps --no-build-isolation -r requirements/dev.txt
	$(PY) -m pip install --require-hashes --no-deps --no-build-isolation -r requirements/stack.txt
	$(PY) -m pip install --no-deps --no-build-isolation -r requirements/stack-vcs.txt
	$(PY) -m pip install --no-deps --no-build-isolation -e .

# Lock integrity (hashes, versions, pins) and the install-command policy for CI, the action, make and scripts.
lock:
	$(PY) scripts/check_lock_freshness.py
	$(PY) scripts/check_dependency_integrity.py

# --disable-socket and the 80% coverage floor come from [tool.pytest.ini_options] addopts.
tests:
	$(PY) -m pytest -q

docs:
	$(PY) scripts/check_docs_alignment.py

claims:
	$(PY) scripts/check_claim_boundaries.py

examples:
	$(PY) scripts/check_examples_parity.py

# Recorded snapshot hashes, and byte-equality with every v* release marker once any exists.
schemas:
	$(PY) scripts/check_schema_snapshots.py

# Third-party actions pinned by SHA with tag comments, pinned runner image, action inputs and docs.
actions:
	$(PY) scripts/check_action.py

build:
	rm -rf $(DIST)
	$(PY) -m build --no-isolation --outdir $(DIST) .

wheel-check:
	$(PY) scripts/check_wheel_contents.py $(DIST)
	$(PY) scripts/check_claim_boundaries.py $(foreach w,$(wildcard $(DIST)/*.whl),--wheel $(w))

# Clean-venv install of the built wheel, then the demo with the network namespace removed.
smoke:
	$(PY) scripts/smoke_install_wheel.py $(DIST)

release-check: check wheel-check smoke

# Order: claim-profile check, release-check, then the authorization gate. The last step fails
# while publication_authorized is false: passing gates never authorizes publication.
release-publish-check:
	$(PY) scripts/check_claim_boundaries.py
	$(MAKE) release-check
	$(PY) scripts/check_release_authorization.py

# Run by .githooks/pre-push.
pre-push: lint claims

clean:
	rm -rf $(DIST) build .coverage .mypy_cache .ruff_cache .pytest_cache
