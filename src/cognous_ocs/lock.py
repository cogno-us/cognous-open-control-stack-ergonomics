"""The component pins `cognous-ocs` was built against.

These are the revisions of the four components listed in the hub's component-lock.json (the hub
commit the fixtures were derived from is recorded in fixtures/customer-service/provenance.json);
the first control-plane revision with `acp run` is 83b49b5e (docs/demo.md and provenance.json
record the pins).
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
            "fa170815ef96a54c233d0dd9da821b60debabf8b",
            "agent-action-manifest",
            "aam",
            "action-manifest",
        ),
        Component(
            "control",
            "control_plane",
            "cogno-us/cognous-control-plane",
            "e77fe0ee8714617899592528e660a80fbb2e864a",
            "agent-control-plane",
            "acp",
            "run-record",
        ),
        Component(
            "replay",
            "replay_bundle",
            "cogno-us/cognous-replay-bundle",
            "53c0cd60d565ea1797ed0be87a2310c5b68b24f6",
            "agent-replay-bundle",
            "arb",
            "replay-bundle",
        ),
        Component(
            "evidence",
            "governance_evidence_pack",
            "cogno-us/cognous-governance-evidence-pack",
            "99ba3211450b25a18007b4b62907b072fe05c964",
            "agent-governance-evidence-pack",
            "agep",
            "evidence-pack",
        ),
    )
}
