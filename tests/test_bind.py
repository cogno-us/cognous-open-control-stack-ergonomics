"""Re-keying the replay bundle and evidence pack to the ids of the control plane's own records."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from ocs import bind
from ocs import fixtures_data as fx

CODES = ["grant_scope_mismatch"]


def make_op(name: str, *, blocked: bool) -> bind.Operation:
    """A run record shaped like `acp run`'s projection, with recognisable ids."""
    proposal = json.loads(fx.read_acp_run_input(f"proposal-{name.replace('_', '-')}.json"))
    reason = "; ".join(CODES)
    record: dict[str, Any] = {
        "run_id": "run-x",
        "frame": {"actor": "urn:actor"},
        "actions": [
            {
                "action_id": proposal["action_id"],
                "tool_name": proposal["adapter_id"],
                "target": proposal["target"],
                "payload": proposal["payload"],
                "proposed_at": "2026-10-05T19:00:00+00:00",
                "reason": "; ".join(proposal["requested_permissions"]),
            }
        ],
        "decisions": [
            {
                "decision_id": f"dec-{name}",
                "result": "block" if blocked else "allow",
                "policy_name": "bounded-authorization-effect/0.1",
                "reason": reason if blocked else "authorized and applied",
                "decided_at": "2026-10-05T19:00:00+00:00",
                "deterministic_fingerprint": f"fp-{name}",
            }
        ],
        "authority_records": []
        if blocked
        else [
            {
                "authority_id": "grant@1",
                "actor": "urn:actor",
                "scope": [proposal["requested_permissions"][0]],
                "source": "bounded-grant",
                "expires_at": None,
                "created_at": "2026-10-05T19:00:00+00:00",
            }
        ],
        "blocked_actions": [
            {
                "blocked_id": f"blk-{name}",
                "action_id": proposal["action_id"],
                "reason": reason,
                "policy_name": "bounded-authorization-effect/0.1",
                "blocked_at": "2026-10-05T19:00:00+00:00",
            }
        ]
        if blocked
        else [],
    }
    envelope = {"reason_codes": CODES if blocked else [], "exit_code": 1 if blocked else 0}
    return bind.Operation(name, envelope, record, f"control/{name}/out/run-record.json")


@pytest.fixture
def ops() -> dict[str, bind.Operation]:
    return {
        "crm_read": make_op("crm_read", blocked=False),
        "notes_search": make_op("notes_search", blocked=False),
        "email_send": make_op("email_send", blocked=True),
    }


def template(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(fx.read_fixture(name))
    return loaded


def test_bundle_carries_the_real_ids_reasons_and_codes_unchanged(
    ops: dict[str, bind.Operation],
) -> None:
    bundle = bind.bind_replay_bundle(template("replay-bundle.json"), ops)
    assert bundle["run_id"] == "run-x"
    assert [p["action_id"] for p in bundle["action_proposals"]] == [
        o.action["action_id"] for o in ops.values()
    ]
    assert [d["decision_id"] for d in bundle["policy_decisions"]] == [
        "dec-crm_read",
        "dec-notes_search",
        "dec-email_send",
    ]
    assert [d["result"] for d in bundle["policy_decisions"]] == ["allow", "allow", "block"]
    assert bundle["blocked_actions"] == [
        {
            "blocked_id": "blk-email_send",
            "action_id": ops["email_send"].action["action_id"],
            "run_id": "run-x",
            "reason": "grant_scope_mismatch",
            "policy_name": "bounded-authorization-effect/0.1",
            "blocked_at": "2026-10-05T19:00:00Z",
        }
    ]
    assert bundle["authority_records"][0]["scope"] == ["crm.read", "notes.search"]
    assert "grant_scope_mismatch" in bundle["final_output"]
    assert bundle["generated_at"] == "2026-10-05T19:01:00Z"
    assert [t["final_result"] for t in bundle["policy_traces"]] == ["allow", "allow", "block"]
    assert bundle["policy_traces"][2]["rules_evaluated"][0]["reason"] == "grant_scope_mismatch"
    assert bundle["metadata"]["synthetic"] is True


def test_binding_does_not_touch_the_template(ops: dict[str, bind.Operation]) -> None:
    original = template("replay-bundle.json")
    frozen = copy.deepcopy(original)
    bind.bind_replay_bundle(original, ops)
    assert original == frozen


def test_pack_points_at_the_real_block_and_the_bound_bundle(ops: dict[str, bind.Operation]) -> None:
    bundle = bind.bind_replay_bundle(template("replay-bundle.json"), ops)
    pack = bind.bind_evidence_pack(
        template("evidence-pack.json"), ops, bundle, "outputs/replay-bundle.json"
    )
    blocked = pack["blocked_actions"][0]
    assert blocked["evidence_reference"] == "control/email_send/out/run-record.json#blk-email_send"
    assert "grant_scope_mismatch" in blocked["reason_summary"]
    assert pack["replay_bundles"][0]["run_id"] == "run-x"
    assert pack["replay_bundles"][0]["evidence_reference"] == "outputs/replay-bundle.json"
    assert pack["risk_register"][0]["evidence_reference"] == blocked["evidence_reference"]
    assert pack["generated_at"] == "2026-10-05T19:02:00Z"


def test_a_record_without_exactly_one_action_is_refused(ops: dict[str, bind.Operation]) -> None:
    bad = copy.deepcopy(ops["crm_read"].record)
    bad["actions"] = []
    ops["crm_read"] = bind.Operation("crm_read", {}, bad, "p")
    with pytest.raises(bind.BindError, match="exactly one entry under 'actions'"):
        bind.bind_replay_bundle(template("replay-bundle.json"), ops)


def test_a_send_without_one_blocked_row_is_refused(ops: dict[str, bind.Operation]) -> None:
    allowed = make_op("email_send", blocked=False)
    ops["email_send"] = allowed
    bundle = bind.bind_replay_bundle(template("replay-bundle.json"), ops)
    with pytest.raises(bind.BindError, match="exactly one blocked_actions row"):
        bind.bind_evidence_pack(template("evidence-pack.json"), ops, bundle, "b.json")


def test_reason_codes_tolerate_a_malformed_envelope() -> None:
    op = bind.Operation("x", {"reason_codes": "not a list"}, {}, "p")
    assert op.reason_codes == []
