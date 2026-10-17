# CLI contract (draft 0.1.0)

Status: draft for owner review. Command names and codes are proposals, not existing Open Control Stack interfaces. Nothing is frozen.

## 1. Purpose

Scripts, CI jobs and people must be able to rely on what `ocs` returns without parsing prose. This document fixes four things: the command set, the exit codes, the JSON result, and the diagnostics file written on failure.

`ocs` reports what the stack's components decided. It never decides. Every result names the component and contract version that produced the underlying verdict.

## 2. Commands

| Command | Purpose | Writes |
|---|---|---|
| `ocs --version` | Print CLI, contract and bundled-component versions | nothing |
| `ocs demo customer-service --out DIR [--clean] [--strict]` | Run the bundled customer-service example offline; succeed only if the blocked email send is evidenced | `DIR` only |
| `ocs validate PATH --kind KIND` | Check one artifact. Kinds: `manifest`, `run-record`, `replay-bundle`, `evidence-pack` | nothing |
| `ocs init workflow --out DIR` | Scaffold a manifest, a policy file and a sample run record | `DIR` only |
| `ocs doctor [--workflow DIR]` | Read-only setup diagnosis | nothing |
| `ocs evidence render PATH --out FILE` | One self-contained HTML view of an evidence pack | `FILE` only |
| `ocs schema export --out DIR` | Export the schema snapshots bundled with this release | `DIR` only |
| `ocs ci` | Entry used by the composite action: validate, gate, write diagnostics | declared output paths |
| `ocs manifest ...`, `ocs replay ...`, `ocs evidence ...` | Thin per-layer passthrough to the component's own public command | as the component does |

Surface rule: no other command families. Additions need an owner decision recorded in the backlog.

Global options: `--format text|json` (default `text`), `--strict`, `--out`, `--no-color`.

## 3. Exit codes

| Exit | Meaning |
|---|---|
| 0 | Succeeded, no blocking finding |
| 1 | Gate or policy check failed |
| 2 | Invalid input, schema failure, unsupported or archival-only version, or path confinement refused |
| 3 | Not applicable to this subject |
| 4 | Internal or execution error |

Rules:
- Outcome `review` exits 0 by default and 1 under `--strict`.
- Outcome `not_evaluated` exits 3.
- Any exit other than 0 writes `ci-diagnostics.json` (section 5) when `--out` or `OCS_DIAGNOSTICS_DIR` names a location.
- The demo exits 0 only when its expected facts hold. Under `--strict` it instead propagates the underlying blocking result.

## 4. JSON result

With `--format json`, one JSON object per run on standard output, nothing else.

```json
{
  "envelope_version": "1",
  "command": "validate",
  "outcome": "invalid",
  "exit_code": 2,
  "reason_codes": ["OCS_KIND_UNREGISTERED"],
  "subject": {"kind": "replay-bundle", "path": "bundle.json", "declared_version": "9.9.9"},
  "verdict_source": {"component": "replay", "contract": "execution-attempt-lifecycle", "contract_version": "0.1.0-draft"},
  "does_not_prove": ["external source linkage", "producer authenticity"],
  "messages": ["replay-bundle version 9.9.9 is not a registered kind/version pair"]
}
```

Rules:
- `outcome` is one of `pass`, `review`, `not_evaluated`, `fail`, `invalid`.
- Consumers choose a parser by `envelope_version`, never by which keys happen to be present. A consumer that does not know the version must treat the result as `invalid`.
- `verdict_source` is required whenever a component produced the verdict. `ocs` adds no verdict of its own.
- `does_not_prove` is required on every `pass`. Success text always states the boundary, for example `artifact-internal validation passed`.
- `messages` are human text with control characters stripped. Nothing machine-readable lives in them.
- Unknown members are refused by `ocs`'s own validators and preserved only under `extensions` with `x-` names, the same model as the ODES contracts.

## 5. Diagnostics file

On any nonzero exit, `ci-diagnostics.json`:

```json
{
  "envelope_version": "1",
  "outcome": "invalid",
  "exit_code": 2,
  "reason_codes": ["OCS_KIND_UNREGISTERED"],
  "report_paths": []
}
```

Written under the declared output directory only. Contains no artifact content, no secrets and no paths outside the workspace.

## 6. Reason codes

Namespace `OCS_*`, stable once published; a code is never reused for a different meaning. Starting set (Phase 2 refines):

| Code | Meaning |
|---|---|
| `OCS_VERSION` | CLI, schema and component versions disagree with the selected lock |
| `OCS_MANIFEST` | Manifest does not parse, or an action lacks a type or authority requirement |
| `OCS_POLICY_BIND` | A manifest action is not covered by a policy rule |
| `OCS_PATHS` | Input or output path outside the workspace, aliased, or containing CR/LF |
| `OCS_SIGNING` | Replay signing configuration missing and not marked demo-only |
| `OCS_REDACTION` | Redaction profile not named or not loadable |
| `OCS_KIND_UNREGISTERED` | (kind, version) pair is not registered; fails closed |
| `OCS_ARCHIVAL_ONLY` | Structural-only historical artifact; exit 2, never a pass |
| `OCS_NOT_APPLICABLE` | Command does not apply to this subject |
| `OCS_INTERNAL` | Unexpected error |

Component reason codes (for example the control plane's `runtime-authority-reasons/0.1` registry) are passed through unchanged in `verdict_source`, not remapped.

## 7. Validation discipline

- The `--kind` argument and the parsed schema version choose the check. A missing `artifact_kind` inside the file cannot bypass it.
- Every (kind, version) pair is registered as full semantic validation or archival-only. An unregistered pair fails closed (exit 2).
- `validate` calls component validators through their ODES contracts. It does not import component internals.
- Each success states what it does not prove.

## 8. Path and write confinement

- Outputs must resolve inside the workspace or the runner temp directory. Symlink and hard-link aliasing is refused.
- Input paths containing CR or LF are refused.
- No input may alias an owned output.
- `init` stops with exit 2 before replacing any differing file; rerunning with identical bytes changes nothing; a partial scaffold resumes by creating only missing files.

## 9. CI action (shape only, Phase 3)

Version-bound: the action verifies the installed CLI version equals the version it was released with; mismatch exits 2. It refuses to run unless a strict evidence input or an explicit non-assurance mode is chosen. Default upload is the evidence pack, summaries and diagnostics, retention 14 days; raw run records need an explicit input. Third-party actions are pinned by commit SHA.

## 10. Claim boundary

No output, document or action label may say certified, compliant or production-ready while publication or production is on HOLD. A build check enforces it from a committed allow-list (Phase 3). `ocs` output labels follow the stack's board, not the tool's own gates.

## 11. Open points (defaults recorded, owner review at the end)

- Command name `ocs`: package-name check pending.
- Python only for the first release; no npm wrapper.
- The `review` outcome exiting 0 by default is a choice; the alternative is exit 1.
