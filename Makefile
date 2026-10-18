# Contributor gates. Activate a virtualenv with `pip install -e ".[dev,stack]"` first.
# Nothing here publishes anything: there is no upload target, and release-publish-check
# fails while release-state.json says publication is not authorized.
PY ?= python
DIST ?= dist

.PHONY: check lint type lock tests docs claims examples build release-check wheel-check smoke \
        release-publish-check pre-push clean

# Fixed order: lint, type, lock freshness, tests (network off, coverage floor), docs alignment,
# claim boundary, examples parity, build.
check: lint type lock tests docs claims examples build

lint:
	ruff check .
	ruff format --check .

type:
	mypy

lock:
	$(PY) scripts/check_lock_freshness.py

# --disable-socket and the 80% coverage floor come from [tool.pytest.ini_options] addopts.
tests:
	$(PY) -m pytest -q

docs:
	$(PY) scripts/check_docs_alignment.py

claims:
	$(PY) scripts/check_claim_boundaries.py

examples:
	$(PY) scripts/check_examples_parity.py

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
