"""The (kind, version) registry behind `cognous-ocs validate` (cli-contract section 7).

Every pair `cognous-ocs` will ever check is listed here, as `full` (a public component command performs
the semantic check) or `archival-only` (known, historical or unsupported by any public
validator: never a pass). A pair that is not listed fails closed. The `--kind` argument and the
version parsed from the file choose the entry; nothing inside the file can select a check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FULL = "full"
ARCHIVAL_ONLY = "archival-only"

KINDS = ("manifest", "run-record", "replay-bundle", "evidence-pack")

# A run record carries no version member in the pinned control plane. Its one registered
# version is this literal; if the file declares a version-like member it must register.
UNVERSIONED = "unversioned"
RUN_RECORD_VERSION_KEYS = (
    "run_record_version",
    "record_version",
    "schema_version",
    "version",
    "artifact_version",
)
VERSION_KEY = {
    "manifest": "manifest_version",
    "replay-bundle": "bundle_version",
    "evidence-pack": "pack_version",
}


@dataclass(frozen=True)
class Entry:
    kind: str
    version: str
    mode: str  # FULL or ARCHIVAL_ONLY
    command: str  # component command (key into cognous_ocs.lock.COMPONENTS)
    args: tuple[str, ...]  # public subcommand, then the path is appended
    note: str


REGISTRY: dict[tuple[str, str], Entry] = {
    (e.kind, e.version): e
    for e in (
        Entry("manifest", "1.0", FULL, "aam", ("validate",), "aam validate"),
        Entry("manifest", "1.1", FULL, "aam", ("validate",), "aam validate (v1.1 rules)"),
        Entry("run-record", UNVERSIONED, FULL, "acp", ("validate-run",), "acp validate-run"),
        Entry("replay-bundle", "0.1", FULL, "arb", ("validate",), "arb validate"),
        Entry(
            "replay-bundle",
            "0.2.0",
            ARCHIVAL_ONLY,
            "arb",
            (),
            "reconstruction bundle: no public command validates it semantically "
            "(arb validate is version-blind and checks the 0.1 shape)",
        ),
        Entry("evidence-pack", "0.1", FULL, "agep", ("validate",), "agep validate"),
    )
}


def declared_version(kind: str, doc: dict[str, Any]) -> str | None:
    """The version the file declares, or None when absent, ambiguous or not a string."""
    if kind == "run-record":
        present = [k for k in RUN_RECORD_VERSION_KEYS if k in doc]
        if not present:
            return UNVERSIONED
        if len(present) > 1:
            return None
        value = doc[present[0]]
        return value if isinstance(value, str) else None
    value = doc.get(VERSION_KEY[kind])
    return value if isinstance(value, str) else None


def lookup(kind: str, version: str | None) -> Entry | None:
    if version is None:
        return None
    return REGISTRY.get((kind, version))
