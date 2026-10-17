"""Derive the bundled customer-service fixtures from the pinned component examples.

The hub example (examples/customer-service-end-to-end) is narrative only, so there is no
concrete file set to copy there. The component repositories each ship a customer-service
example, but with unrelated ids. This script keeps the manifest byte-for-byte and re-keys
the other three so one synthetic run is consistent across all four layers, then writes
provenance.json (source commits, source hashes, bundled hashes).

Usage (offline, read-only toward the checkouts):
  python tools/derive_fixtures.py --hub HUBDIR --components DIR --out src/ocs/fixtures/customer-service

DIR holds checkouts named after the repositories, each at the commit pinned in ocs.lock.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ocs.lock import COMPONENTS  # noqa: E402

EXAMPLE_VERSION = "0.1.0"
SOURCES = {
    "manifest.json": ("action_manifest", "examples/customer_service_agent.manifest.json"),
    "run-record.json": ("control_plane", "examples/sample_run_record.json"),
    "replay-bundle.json": ("replay_bundle", "examples/customer_service_replay_bundle.json"),
    "evidence-pack.json": (
        "governance_evidence_pack",
        "examples/customer_service_agent_evidence_pack.json",
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--components", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    hub = Path(args.hub)
    comp = Path(args.components)
    O = Path(args.out)
    O.mkdir(parents=True, exist_ok=True)
    by_key = {c.key: c for c in COMPONENTS.values()}
    checkouts = {}
    for key, c in by_key.items():
        d = comp / c.repository.split("/")[1]
        if git_head(d) != c.sha:
            print(f"{d} is not at pinned {c.sha}", file=sys.stderr)
            return 2
        checkouts[key] = d

    def read(key: str, rel: str) -> bytes:
        return (checkouts[key] / rel).read_bytes()

    def w(name: str, obj: object) -> None:
        (O / name).write_text(
            json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    (O / "manifest.json").write_bytes(read("action_manifest", SOURCES["manifest.json"][1]))

    RUN = "run-cs-demo-001"
    FR = "frame-cs-demo-001"
    # 2 run record
    rr = json.loads(read("control_plane", SOURCES["run-record.json"][1]))

    def ids(n, p):
        return f"{p}-cs-demo-{n:03d}"

    rr["run_id"] = RUN
    rr["task"] = "Look up a customer, search account notes, and attempt to send a follow-up email."
    rr["created_at"] = "2026-07-01T10:15:00+00:00"
    rr["frame"].update(
        frame_id=FR,
        task=rr["task"],
        actor="customer-service-agent",
        allowed_tools=["crm_read", "notes_search"],
        blocked_tools=["email_send"],
        policy_version="cs-policy-demo-1",
        created_at="2026-07-01T10:15:00+00:00",
    )
    a_crm, a_notes, a_send = rr["actions"][0], copy.deepcopy(rr["actions"][0]), rr["actions"][1]
    a_crm.update(
        action_id=ids(1, "action"),
        run_id=RUN,
        target="customer:C-7823",
        proposed_at="2026-07-01T10:15:01+00:00",
    )
    a_notes.update(
        action_id=ids(2, "action"),
        run_id=RUN,
        tool_name="notes_search",
        target="notes:C-7823",
        payload={"query": "return request", "limit": 5},
        proposed_at="2026-07-01T10:15:03+00:00",
        reason="Search account notes for the open return request.",
    )
    a_send.update(
        action_id=ids(3, "action"),
        run_id=RUN,
        target="customer-placeholder@example.com",
        payload={"subject": "Your return has been processed", "body": "<draft>"},
        proposed_at="2026-07-01T10:15:08+00:00",
        reason="Send the prepared email draft to the customer.",
    )
    rr["actions"] = [a_crm, a_notes, a_send]
    d_crm, d_send = rr["decisions"]
    d_notes = copy.deepcopy(d_crm)
    d_crm.update(
        decision_id=ids(1, "decision"),
        action_id=a_crm["action_id"],
        run_id=RUN,
        decided_at="2026-07-01T10:15:02+00:00",
        deterministic_fingerprint="fp-demo-crm-read",
    )
    d_notes.update(
        decision_id=ids(2, "decision"),
        action_id=a_notes["action_id"],
        run_id=RUN,
        reason="Tool 'notes_search' is allowed and action type is 'read'.",
        decided_at="2026-07-01T10:15:04+00:00",
        deterministic_fingerprint="fp-demo-notes-search",
    )
    d_send.update(
        decision_id=ids(3, "decision"),
        action_id=a_send["action_id"],
        run_id=RUN,
        reason="Tool 'email_send' is explicitly blocked in this frame: outbound-send authority is missing.",
        decided_at="2026-07-01T10:15:09+00:00",
        deterministic_fingerprint="fp-demo-email-send",
    )
    rr["decisions"] = [d_crm, d_notes, d_send]
    au = rr["authority_records"][0]
    au.update(
        authority_id=ids(1, "authority"),
        run_id=RUN,
        actor="customer-service-agent",
        created_at="2026-07-01T10:15:00+00:00",
    )
    rl = rr["reliance_records"][0]
    rl2 = copy.deepcopy(rl)
    rl.update(
        reliance_id=ids(1, "reliance"),
        run_id=RUN,
        source_name="crm_read",
        referenced_action_id=a_crm["action_id"],
        created_at="2026-07-01T10:15:02+00:00",
    )
    rl2.update(
        reliance_id=ids(2, "reliance"),
        run_id=RUN,
        source_name="notes_search",
        scope="account notes for customer C-7823",
        referenced_action_id=a_notes["action_id"],
        created_at="2026-07-01T10:15:04+00:00",
    )
    rr["reliance_records"] = [rl, rl2]
    b = rr["blocked_actions"][0]
    b.update(
        blocked_id=ids(1, "blocked"),
        action_id=a_send["action_id"],
        run_id=RUN,
        reason=d_send["reason"],
        blocked_at="2026-07-01T10:15:09+00:00",
    )
    rr["final_output"] = (
        "Draft prepared but email send blocked: outbound-send authority is missing."
    )
    w("run-record.json", rr)

    # 3 replay bundle
    rb = json.loads(read("replay_bundle", SOURCES["replay-bundle.json"][1]))
    rb.update(
        bundle_id="bundle-cs-demo-001",
        run_id=RUN,
        generated_at="2026-07-01T10:16:00Z",
        producer="ocs-demo-synthetic",
    )
    rb["frame"].update(
        frame_id=FR,
        task=rr["task"],
        allowed_tools=["crm_read", "notes_search"],
        blocked_tools=["email_send"],
        policy_version="cs-policy-demo-1",
        created_at="2026-07-01T10:15:00Z",
    )
    p = rb["action_proposals"]
    p0 = p[0]
    p1 = copy.deepcopy(p[0])
    p2 = p[1]
    p0.update(
        action_id=a_crm["action_id"],
        run_id=RUN,
        tool_name="crm_read",
        target="customer:C-7823",
        payload={"fields": ["name", "email", "account_status"]},
        proposed_at="2026-07-01T10:15:01Z",
        reason=a_crm["reason"],
    )
    p1.update(
        action_id=a_notes["action_id"],
        run_id=RUN,
        tool_name="notes_search",
        target="notes:C-7823",
        payload={"query": "return request", "limit": 5},
        proposed_at="2026-07-01T10:15:03Z",
        reason=a_notes["reason"],
    )
    p2.update(
        action_id=a_send["action_id"],
        run_id=RUN,
        tool_name="email_send",
        target="customer-placeholder@example.com",
        payload={"subject": "Your return has been processed", "body": "<draft>"},
        proposed_at="2026-07-01T10:15:08Z",
        reason=a_send["reason"],
    )
    rb["action_proposals"] = [p0, p1, p2]
    pd = rb["policy_decisions"]
    q0 = pd[0]
    q1 = copy.deepcopy(pd[0])
    q2 = pd[1]
    q0.update(
        decision_id=d_crm["decision_id"],
        action_id=p0["action_id"],
        run_id=RUN,
        policy_name="read_allowed_tool_policy",
        reason=d_crm["reason"],
        decided_at="2026-07-01T10:15:02Z",
        deterministic_fingerprint=d_crm["deterministic_fingerprint"],
        trace_id="trace-cs-demo-001",
    )
    q1.update(
        decision_id=d_notes["decision_id"],
        action_id=p1["action_id"],
        run_id=RUN,
        policy_name="read_allowed_tool_policy",
        reason=d_notes["reason"],
        decided_at="2026-07-01T10:15:04Z",
        deterministic_fingerprint=d_notes["deterministic_fingerprint"],
        trace_id="trace-cs-demo-002",
    )
    q2.update(
        decision_id=d_send["decision_id"],
        action_id=p2["action_id"],
        run_id=RUN,
        policy_name="blocked_tool_policy",
        reason=d_send["reason"],
        decided_at="2026-07-01T10:15:09Z",
        deterministic_fingerprint=d_send["deterministic_fingerprint"],
        trace_id="trace-cs-demo-003",
    )
    rb["policy_decisions"] = [q0, q1, q2]
    tr = rb["policy_traces"]
    t0 = tr[0]
    t1 = copy.deepcopy(tr[0])
    t2 = tr[1]
    for t, q, pp, n in ((t0, q0, p0, 1), (t1, q1, p1, 2), (t2, q2, p2, 3)):
        t.update(
            trace_id=f"trace-cs-demo-{n:03d}",
            run_id=RUN,
            action_id=pp["action_id"],
            evaluated_at=q["decided_at"],
            final_result=q["result"],
            deterministic_fingerprint=q["deterministic_fingerprint"],
        )
    t0["rules_evaluated"][1]["reason"] = (
        "AuthorityRecord " + au["authority_id"] + " grants read scope."
    )
    t1["rules_evaluated"] = copy.deepcopy(t0["rules_evaluated"])
    t2["rules_evaluated"] = [
        {
            "rule_name": "require-external-send-authority",
            "matched": False,
            "result": "block",
            "reason": "No authority record found granting outbound-send scope for this run.",
        }
    ]
    rb["policy_traces"] = [t0, t1, t2]
    rb["blocked_actions"] = [
        {
            "blocked_id": b["blocked_id"],
            "action_id": p2["action_id"],
            "run_id": RUN,
            "reason": b["reason"],
            "policy_name": "blocked_tool_policy",
            "blocked_at": "2026-07-01T10:15:09Z",
        }
    ]
    rb["authority_records"] = [
        {
            "authority_id": au["authority_id"],
            "run_id": RUN,
            "actor": "customer-service-agent",
            "scope": ["read"],
            "source": "user_consent",
            "expires_at": None,
            "created_at": "2026-07-01T10:15:00Z",
        }
    ]
    rb["reliance_records"] = [
        {
            "reliance_id": rl["reliance_id"],
            "run_id": RUN,
            "source_name": "crm_read",
            "source_type": "tool",
            "scope": rl["scope"],
            "referenced_action_id": p0["action_id"],
            "created_at": "2026-07-01T10:15:02Z",
        },
        {
            "reliance_id": rl2["reliance_id"],
            "run_id": RUN,
            "source_name": "notes_search",
            "source_type": "tool",
            "scope": rl2["scope"],
            "referenced_action_id": p1["action_id"],
            "created_at": "2026-07-01T10:15:04Z",
        },
    ]
    rb["final_output"] = rr["final_output"]
    rb["validation_report"]["checked_id"] = "bundle-cs-demo-001"
    rb["metadata"] = {"synthetic": True, "note": "Synthetic demo fixture; no real customer data."}
    w("replay-bundle.json", rb)

    # 4 evidence pack
    ep = json.loads(read("governance_evidence_pack", SOURCES["evidence-pack.json"][1]))
    ep.update(
        pack_id="ep-cs-demo-001",
        title="Customer Service Agent: Demo Evidence Pack (synthetic)",
        generated_at="2026-07-01T10:20:00Z",
    )
    ep["deployment_context"].update(
        environment="staging",
        deployment_name="cs-agent-demo",
        notes="Synthetic demo fixture. Not a deployment record.",
    )
    ep["tool_inventory"] = [
        {
            "tool_name": "crm_read",
            "description": "Read customer records from the CRM.",
            "external_system": "CRM Platform",
            "data_classification": "confidential",
            "access_mode": "read-only",
            "control_status": "implemented",
        },
        {
            "tool_name": "notes_search",
            "description": "Search account notes.",
            "external_system": "CRM Platform",
            "data_classification": "confidential",
            "access_mode": "read-only",
            "control_status": "implemented",
        },
        {
            "tool_name": "email_send",
            "description": "Send outbound email to customers.",
            "external_system": "Email Gateway",
            "data_classification": "confidential",
            "access_mode": "write",
            "control_status": "implemented",
        },
    ]
    ai = ep["action_inventory"]
    ai = [
        {**ai[0], "action_name": "crm_read", "tool_name": "crm_read"},
        {
            **ai[0],
            "action_name": "notes_search",
            "tool_name": "notes_search",
            "description": "Search customer account notes.",
        },
        {**ai[1], "action_name": "email_send", "tool_name": "email_send"},
    ]
    ep["action_inventory"] = ai
    ep["blocked_actions"] = [
        {
            "action_name": "email_send",
            "action_type": "external_send",
            "tool_name": "email_send",
            "count": 1,
            "reason_summary": "Outbound email send blocked: outbound-send authority is missing.",
            "evidence_reference": "run-record.json#" + b["blocked_id"],
        }
    ]
    ep["reliance_summary"] = [
        {
            "source_name": "CRM Platform",
            "source_type": "database",
            "count": 2,
            "scope_summary": "One CRM read and one account-notes search for customer C-7823.",
            "evidence_reference": "run-record.json#reliance_records",
        }
    ]
    ep["replay_bundles"] = [
        {
            "bundle_id": rb["bundle_id"],
            "run_id": RUN,
            "status": "available",
            "generated_at": rb["generated_at"],
            "signed": False,
            "redacted": False,
            "validation_status": "valid",
            "evidence_reference": "replay-bundle.json",
        }
    ]
    ep["validation_summary"] = {
        "valid_bundle_count": 1,
        "invalid_bundle_count": 0,
        "warning_count": 1,
        "error_count": 0,
        "summary": "One replay bundle validated with one warning (unsigned).",
    }
    ep["redaction_exports"] = []
    ep["risk_register"] = [
        ep["risk_register"][0]
        | {
            "risk_id": "risk-cs-demo-001",
            "description": "Agent may attempt to send emails without outbound-send authority. Blocked once in this synthetic run.",
            "mitigation": "Outbound send is blocked without authority; the block is recorded.",
            "evidence_reference": "run-record.json#" + b["blocked_id"],
        }
    ]
    ep["metadata"] = {"synthetic": True, "notes": "Synthetic demo fixture."}
    w("evidence-pack.json", ep)

    hub_dir = hub / "examples" / "customer-service-end-to-end"
    prov = {
        "example_name": "customer-service",
        "example_version": EXAMPLE_VERSION,
        "synthetic": True,
        "note": (
            "The hub example is narrative only. manifest.json is a byte copy of the component "
            "example; the other three are derived from component examples and re-keyed so one "
            "synthetic run is consistent across layers. Produced by tools/derive_fixtures.py."
        ),
        "hub": {
            "repository": "cogno-us/cognous-open-control-stack",
            "commit": git_head(hub),
            "example_path": "examples/customer-service-end-to-end",
            "example_files": {p.name: sha256(p) for p in sorted(hub_dir.iterdir())},
            "component_lock_sha256": sha256(hub / "component-lock.json"),
        },
        "sources": {
            name: {
                "component": key,
                "repository": by_key[key].repository,
                "commit": by_key[key].sha,
                "path": rel,
                "sha256": sha256(checkouts[key] / rel),
            }
            for name, (key, rel) in SOURCES.items()
        },
        "bundled": {name: sha256(O / name) for name in SOURCES},
        "byte_equal_to_source": ["manifest.json"],
    }
    (O / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
