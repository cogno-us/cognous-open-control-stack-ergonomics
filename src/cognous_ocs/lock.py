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
            "0a41faa972699f7ec3063967a14cd9bdd3e67d27",
            "agent-control-plane",
            "acp",
            "run-record",
        ),
        Component(
            "replay",
            "replay_bundle",
            "cogno-us/cognous-replay-bundle",
            "028f63d1c001f99580583888fa4b4e46d77a267b",
            "agent-replay-bundle",
            "arb",
            "replay-bundle",
        ),
        Component(
            "evidence",
            "governance_evidence_pack",
            "cogno-us/cognous-governance-evidence-pack",
            "a3ebbb90559a44a88855139a182649d3792bb1ca",
            "agent-governance-evidence-pack",
            "agep",
            "evidence-pack",
        ),
    )
}
