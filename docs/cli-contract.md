# CLI contract (draft 0.1.0)

Implementation corrections (Phase 1) are marked **Correction** below.

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
  "does_not_prove": [],
  "messages": ["replay-bundle version 9.9.9 is not a registered kind/version pair"]
}
```

Rules:
- `outcome` is one of `pass`, `review`, `not_evaluated`, `fail`, `invalid`, `error`. **Correction:** `error` is added because exit 4 (internal or execution error) had no outcome in the first draft. Exits: pass 0, review 0 (1 under `--strict`), not_evaluated 3, fail 1, invalid 2, error 4.
- Consumers choose a parser by `envelope_version`, never by which keys happen to be present. A consumer that does not know the version must treat the result as `invalid`.
- `verdict_source` is required whenever a component produced the verdict. `ocs` adds no verdict of its own. **Correction:** a command that chains several components (`demo`) names in `verdict_source` the component whose decision it evidences (the control plane, for the blocked send) and lists every step under `extensions["x-steps"]`. A command that only reads and formats (`evidence render`) produced no component verdict and carries no `verdict_source`; its `does_not_prove` says the pack was not validated by that command. `contract_version` is the installed component package version, not an ODES draft label.
- **Correction (Phase 2):** a registry refusal (`OCS_KIND_UNREGISTERED`, `OCS_ARCHIVAL_ONLY`) is made by `ocs` before any component runs, so it carries no `verdict_source`; the first draft's example showed one. In `validate`, a component exit 0 is `pass`, exit 1 or 2 is `invalid` (exit 2: the artifact was not accepted; the component's output is in `extensions["x-component-output"]`), a crash or any other code is `error` (exit 4). The component's verdict is relayed, never remapped to a stronger one.
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
| `OCS_INPUT_INVALID` | Input does not parse, lacks a field the command needs, or the command line is malformed (**Correction:** added) |
| `OCS_DEMO_EXPECTATION` | A demo step returned an unexpected exit code or an expected artifact fact did not hold (**Correction:** added) |
| `OCS_BLOCKING_FINDING` | A component recorded a blocking decision; propagated as exit 1 under `--strict` (**Correction:** added) |

Component reason codes (for example the control plane's `runtime-authority-reasons/0.1` registry) are passed through unchanged in `verdict_source`, not remapped.

## 7. Validation discipline

- The `--kind` argument and the parsed schema version choose the check. A missing `artifact_kind` inside the file cannot bypass it.
- Every (kind, version) pair is registered as full semantic validation or archival-only. An unregistered pair fails closed (exit 2).
- `validate` calls component validators through their ODES contracts. It does not import component internals.
- Each success states what it does not prove.
- **Correction (Phase 2):** the registry lives in `src/ocs/registry.py` and is listed in `docs/validate.md`. A missing, non-string or ambiguous version member matches no pair. A file whose `artifact_kind` disagrees with `--kind` is refused. Duplicate JSON members, NaN/Infinity and non-UTF-8 are refused as `OCS_INPUT_INVALID`. Pins are checked only for the one component the pair needs.

- **Correction (Phase 2):** `init` writes four files, not three: `workflow.json` names the manifest, policy and run record and carries the signing and redaction settings `doctor` reads. A differing managed file is reported as `OCS_PATHS` (the same code the demo uses for a refused output directory). `doctor` never writes, including `ci-diagnostics.json`, and a check that cannot run because an earlier one failed is SKIP. Details in `docs/init-doctor.md`.

## 8. Path and write confinement

- Outputs must resolve inside the workspace or the runner temp directory. Symlink and hard-link aliasing is refused.
- Input paths containing CR or LF are refused.
- No input may alias an owned output.
- `init` stops with exit 2 before replacing any differing file; rerunning with identical bytes changes nothing; a partial scaffold resumes by creating only missing files.

## 9. CI action (shape only, Phase 3)

Version-bound: the action verifies the installed CLI version equals the version it was released with; mismatch exits 2. It refuses to run unless a strict evidence input or an explicit non-assurance mode is chosen. Default upload is the evidence pack, summaries and diagnostics, retention 14 days; raw run records need an explicit input. Third-party actions are pinned by commit SHA.

## 10. Claim boundary

No output, document or action label may say certified, compliant or production-ready while publication or production is on HOLD. `scripts/check_claim_boundaries.py` enforces it in `make check` and CI. State comes from the committed `release-state.json` (publication and production authorization, both false while on HOLD); a missing or malformed file counts as HOLD. A match is excused only by an exact entry in `scripts/claim_boundaries_allowlist.json`, and an entry that matches nothing fails the build. `ocs` output labels follow the stack's board, not the tool's own gates.

## 11. Open points (defaults recorded, owner review at the end)

- Command name `ocs`: package-name check pending.
- Python only for the first release; no npm wrapper.
- The `review` outcome exiting 0 by default is a choice; the alternative is exit 1.
