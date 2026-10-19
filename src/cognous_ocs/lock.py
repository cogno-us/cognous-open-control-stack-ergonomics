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
            "6a6a477495ac24e813f12bb0fe314acfe8bac9cf",
            "agent-replay-bundle",
            "arb",
            "replay-bundle",
        ),
        Component(
            "evidence",
            "governance_evidence_pack",
            "cogno-us/cognous-governance-evidence-pack",
            "fd030615a782a064b89c57e8e40d7f220f99ff9e",
            "agent-governance-evidence-pack",
            "agep",
            "evidence-pack",
        ),
    )
}
