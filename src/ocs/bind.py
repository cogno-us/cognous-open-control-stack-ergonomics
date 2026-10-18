"""Re-key the bundled replay bundle and evidence pack to the ids of the real control-plane runs.

`acp run` writes fresh random decision and blocked ids on every run, so a static fixture cannot
carry them. This module copies those ids, times, reasons and reason codes from the run records the
control plane produced into copies of the replay-bundle and evidence-pack templates. It adds no
decision and rewrites no reason code: the component's strings are placed in the documents
unchanged. Everything else in the two templates (narrative, reliance rows, inventories) stays
synthetic, and the demo says so.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# The three operations of the scenario, in the order the templates list them.
OPERATIONS = ("crm_read", "notes_search", "email_send")
SEND = "email_send"


class BindError(ValueError):
    """A control-plane run record does not have the shape the binding needs."""


@dataclass(frozen=True)
class Operation:
    """What one `acp run` produced, as read back from its out-dir."""

    name: str
    envelope: dict[str, Any]
    record: dict[str, Any]
    record_path: str  # path of run-record.json, relative to the demo directory

    @property
    def action(self) -> dict[str, Any]:
        return _only(self.record, "actions")

    @property
    def decision(self) -> dict[str, Any]:
        return _only(self.record, "decisions")

    @property
    def blocked(self) -> list[dict[str, Any]]:
        rows = self.record.get("blocked_actions", [])
        return [r for r in rows if isinstance(r, dict)]

    @property
    def reason_codes(self) -> list[str]:
        codes = self.envelope.get("reason_codes", [])
        return [str(c) for c in codes] if isinstance(codes, list) else []


def _only(record: dict[str, Any], key: str) -> dict[str, Any]:
    rows = record.get(key)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise BindError(f"run record must hold exactly one entry under {key!r}")
    row: dict[str, Any] = rows[0]
    return row


def _zulu(value: str, plus_seconds: int = 0) -> str:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(seconds=plus_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def bind_replay_bundle(template: dict[str, Any], ops: dict[str, Operation]) -> dict[str, Any]:
    """A copy of the replay-bundle template carrying the real runs' ids, times and reasons."""
    bundle = copy.deepcopy(template)
    send = ops[SEND]
    first = ops[OPERATIONS[0]]
    run_id = str(first.record["run_id"])
    started = _zulu(str(first.decision["decided_at"]))
    bundle["run_id"] = run_id
    bundle["generated_at"] = _zulu(started, 60)
    bundle["frame"].update(
        frame_id=f"frame-{run_id}",
        actor=str(first.record["frame"]["actor"]),
        policy_version=str(first.decision["policy_name"]),
        created_at=started,
    )
    proposals: dict[str, dict[str, Any]] = {p["tool_name"]: p for p in bundle["action_proposals"]}
    decisions: dict[str, dict[str, Any]] = {}
    traces: dict[str, dict[str, Any]] = {}
    for original in bundle["policy_decisions"]:
        tool = next(t for t, p in proposals.items() if p["action_id"] == original["action_id"])
        decisions[tool] = original
    for original in bundle["policy_traces"]:
        tool = next(t for t, p in proposals.items() if p["action_id"] == original["action_id"])
        traces[tool] = original
    for name in OPERATIONS:
        op = ops[name]
        action, decision = op.action, op.decision
        proposal, policy, trace = proposals[name], decisions[name], traces[name]
        proposal.update(
            action_id=action["action_id"],
            run_id=run_id,
            target=action["target"],
            payload=copy.deepcopy(action["payload"]),
            proposed_at=_zulu(str(action["proposed_at"])),
        )
        policy.update(
            decision_id=decision["decision_id"],
            action_id=action["action_id"],
            run_id=run_id,
            result=decision["result"],
            policy_name=decision["policy_name"],
            reason=decision["reason"],
            decided_at=_zulu(str(decision["decided_at"])),
            deterministic_fingerprint=decision["deterministic_fingerprint"],
        )
        outcome = "block" if decision["result"] == "block" else "allow"
        trace.update(
            run_id=run_id,
            action_id=action["action_id"],
            evaluated_at=_zulu(str(decision["decided_at"])),
            final_result=outcome,
            deterministic_fingerprint=decision["deterministic_fingerprint"],
            rules_evaluated=[
                {
                    "rule_name": decision["policy_name"],
                    "matched": outcome == "block",
                    "result": outcome,
                    "reason": decision["reason"],
                }
            ],
        )
    bundle["action_proposals"] = [proposals[n] for n in OPERATIONS]
    bundle["policy_decisions"] = [decisions[n] for n in OPERATIONS]
    bundle["policy_traces"] = [traces[n] for n in OPERATIONS]
    bundle["blocked_actions"] = [
        {
            "blocked_id": row["blocked_id"],
            "action_id": row["action_id"],
            "run_id": run_id,
            "reason": row["reason"],
            "policy_name": row["policy_name"],
            "blocked_at": _zulu(str(row["blocked_at"])),
        }
        for name in OPERATIONS
        for row in ops[name].blocked
    ]
    authority: dict[str, dict[str, Any]] = {}
    for name in OPERATIONS:
        for row in ops[name].record.get("authority_records", []):
            entry = authority.setdefault(
                row["authority_id"],
                {
                    "authority_id": row["authority_id"],
                    "run_id": run_id,
                    "actor": row["actor"],
                    "scope": [],
                    "source": row["source"],
                    "expires_at": row.get("expires_at"),
                    "created_at": _zulu(str(row["created_at"])),
                },
            )
            entry["scope"] = sorted({*entry["scope"], *row["scope"]})
    bundle["authority_records"] = list(authority.values())
    for rely in bundle["reliance_records"]:
        op = ops[str(rely["source_name"])]
        rely.update(
            run_id=run_id,
            referenced_action_id=op.action["action_id"],
            created_at=_zulu(str(op.decision["decided_at"])),
        )
    codes = ", ".join(send.reason_codes) or "no reason code"
    bundle["final_output"] = (
        "Customer lookup and notes search ran; "
        f"the email send was blocked by the control plane ({codes})."
    )
    bundle["validation_report"]["checked_id"] = bundle["bundle_id"]
    bundle["metadata"] = {
        "synthetic": True,
        "note": (
            "Synthetic demo bundle re-keyed to the ids of three real acp runs; "
            "reliance rows and narrative are not produced by acp run."
        ),
    }
    return bundle


def bind_evidence_pack(
    template: dict[str, Any], ops: dict[str, Operation], bundle: dict[str, Any], bundle_path: str
) -> dict[str, Any]:
    """A copy of the evidence-pack template pointing at the real send block and the bound bundle."""
    pack = copy.deepcopy(template)
    send = ops[SEND]
    if len(send.blocked) != 1:
        raise BindError("the email_send run record must hold exactly one blocked_actions row")
    row = send.blocked[0]
    codes = ", ".join(send.reason_codes) or "no reason code"
    reference = f"{send.record_path}#{row['blocked_id']}"
    pack["generated_at"] = _zulu(str(bundle["generated_at"]), 60)
    pack["blocked_actions"] = [
        {
            "action_name": SEND,
            "action_type": "external_send",
            "tool_name": SEND,
            "count": 1,
            "reason_summary": f"Outbound email send blocked by the control plane: {codes}.",
            "evidence_reference": reference,
        }
    ]
    for risk in pack["risk_register"]:
        risk["evidence_reference"] = reference
    pack["replay_bundles"] = [
        {
            "bundle_id": bundle["bundle_id"],
            "run_id": bundle["run_id"],
            "status": "available",
            "generated_at": bundle["generated_at"],
            "signed": False,
            "redacted": False,
            "validation_status": "valid",
            "evidence_reference": bundle_path,
        }
    ]
    pack["metadata"] = {
        "synthetic": True,
        "notes": (
            "Synthetic demo pack pointing at the real send block; "
            "inventories are not produced by acp run."
        ),
    }
    return pack
