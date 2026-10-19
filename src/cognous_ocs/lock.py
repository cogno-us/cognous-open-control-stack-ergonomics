"""The component pins `cognous-ocs` was built against.

These are the current main revisions of the four components. They differ from the entries in the
hub's component-lock.json (at the hub commit recorded in fixtures/customer-service/provenance.json)
until the hub lock is updated in a later step; the first control-plane revision with `acp run` is
83b49b5e (docs/demo.md and provenance.json record the pins and the lock values).
`cognous-ocs` calls each component only through its public command; it never imports component
internals.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Component:
    layer: str  # declare, control, replay, evidence
    key: str  # name in the hub's component-lock.json
    repository: str
    sha: str
    distribution: str  # installed distribution name
    command: str  # public command
    contract: str  # label used in verdict_source


COMPONENTS: dict[str, Component] = {
    c.command: c
    for c in (
        Component(
            "declare",
            "action_manifest",
            "cogno-us/cognous-action-manifest",
            "ff5490828b2bf7f6a976da209f3f533701855687",
            "agent-action-manifest",
            "aam",
            "action-manifest",
        ),
        Component(
            "control",
            "control_plane",
            "cogno-us/cognous-control-plane",
            "2bac7875391cad0a431fa0d75208b7ade39743e3",
            "agent-control-plane",
            "acp",
            "run-record",
        ),
        Component(
            "replay",
            "replay_bundle",
            "cogno-us/cognous-replay-bundle",
            "2a7963b18d96eb2e9e4fdb1a09c7588cf5c0ef00",
            "agent-replay-bundle",
            "arb",
            "replay-bundle",
        ),
        Component(
            "evidence",
            "governance_evidence_pack",
            "cogno-us/cognous-governance-evidence-pack",
            "80e24650bb089d3a0cb9c0063c5b4bcc4a436123",
            "agent-governance-evidence-pack",
            "agep",
            "evidence-pack",
        ),
    )
}
