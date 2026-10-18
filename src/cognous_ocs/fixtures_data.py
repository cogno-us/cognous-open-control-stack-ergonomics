"""Bundled example fixtures, loaded as package data with importlib.resources."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

from cognous_ocs import EXAMPLE_NAME

FIXTURE_FILES = ("manifest.json", "run-record.json", "replay-bundle.json", "evidence-pack.json")
# Inputs of the demo's real control step (`acp run`), under acp-run/. Provenance records their
# hashes under provenance["acp_run"]["bundled"].
ACP_RUN_DIR = "acp-run"
ACP_RUN_FILES = (
    "manifest.json",
    "authority-context.json",
    "resolver-state.json",
    "proposal-crm-read.json",
    "proposal-notes-search.json",
    "proposal-email-send.json",
)


class FixtureIntegrityError(Exception):
    """A bundled fixture does not match its recorded hash."""


def _root() -> Any:
    return files("cognous_ocs").joinpath("fixtures", EXAMPLE_NAME)


def provenance() -> dict[str, Any]:
    data = json.loads(_root().joinpath("provenance.json").read_bytes())
    if not isinstance(data, dict):
        raise FixtureIntegrityError("provenance.json is not an object")
    return data


def example_version() -> str:
    return str(provenance()["example_version"])


def read_fixture(name: str) -> bytes:
    """Return a fixture's bytes after checking them against the recorded hash."""
    if name not in FIXTURE_FILES:
        raise FixtureIntegrityError(f"unknown fixture {name!r}")
    data: bytes = _root().joinpath(name).read_bytes()
    recorded = provenance()["bundled"].get(name)
    if hashlib.sha256(data).hexdigest() != recorded:
        raise FixtureIntegrityError(f"fixture {name} does not match its recorded hash")
    return data


def read_acp_run_input(name: str) -> bytes:
    """Return one `acp run` input's bytes after checking them against the recorded hash."""
    if name not in ACP_RUN_FILES:
        raise FixtureIntegrityError(f"unknown acp-run input {name!r}")
    data: bytes = _root().joinpath(ACP_RUN_DIR, name).read_bytes()
    recorded = provenance()["acp_run"]["bundled"].get(name)
    if hashlib.sha256(data).hexdigest() != recorded:
        raise FixtureIntegrityError(f"acp-run input {name} does not match its recorded hash")
    return data
