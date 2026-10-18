"""The component pins `ocs` was built against.

These mirror the entries in the hub's component-lock.json at the hub commit recorded in
fixtures/customer-service/provenance.json, except the control plane: it is pinned to the first
revision with `acp run` (83b49b5e), which is ahead of the hub lock (docs/demo.md and
provenance.json record both). `ocs` calls each component only through its public command; it
never imports component internals.
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
            "46c950bed37fe3812000895430bc0312d29e37ce",
            "agent-action-manifest",
            "aam",
            "action-manifest",
        ),
        Component(
            "control",
            "control_plane",
            "cogno-us/cognous-control-plane",
            "83b49b5e61cdacee916b502cdea775aefc72a064",
            "agent-control-plane",
            "acp",
            "run-record",
        ),
        Component(
            "replay",
            "replay_bundle",
            "cogno-us/cognous-replay-bundle",
            "459e4ba62fca49364aebb0050cd5fb2dd5a71bfa",
            "agent-replay-bundle",
            "arb",
            "replay-bundle",
        ),
        Component(
            "evidence",
            "governance_evidence_pack",
            "cogno-us/cognous-governance-evidence-pack",
            "b4baccd823d2a73be276c1de745b19cf7c56a0d6",
            "agent-governance-evidence-pack",
            "agep",
            "evidence-pack",
        ),
    )
}
