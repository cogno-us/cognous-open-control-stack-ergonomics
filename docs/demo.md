# The customer-service demo

```
cognous-ocs demo customer-service --out DIR [--clean] [--strict]
```

Runs the bundled customer-service example end to end, offline, and exits 0 only if the blocked email send is evidenced in every layer. It needs no network and no key. The components are installed with `pip install ".[stack]"`. All four are pinned to the revisions listed in the hub's `component-lock.json` (next section).

## Pins and the hub lock

The demo's control step runs `acp run`, which first exists in control-plane revision `83b49b5e` (cogno-us/cognous-control-plane#46). The four component pins are in `src/cognous_ocs/lock.py` and the `stack` extra, and each equals the entry in the hub's `component-lock.json`: see the hub lock for the revisions, and the [README pins table](../README.md#pins-and-the-hub-lock) for the comparison. This page does not repeat them.

It is reversible by repinning: set the four lines of the `stack` extra in `pyproject.toml` and `src/cognous_ocs/lock.py` to other commits, regenerate `requirements/stack.txt` with the documented `uv` command (see [testing.md](testing.md)), and rerun `tools/derive_fixtures.py`. `provenance.json` records the pins and the lock commit under `control_plane_pin` and `hub`.

Update 2026-10-22 (pin move to the hub lock at hub commit `f413d1a7274d`). The four pins were moved to the entries in the hub `component-lock.json` at that commit (lock sha256 `71952371c312a64f322d45f792866f063677f5c1c9cdf0facc353229cf9c8f4d`), and `provenance.json` was regenerated with `tools/derive_fixtures.py`. The bundled fixture files are byte-identical to before; only the recorded commits, the hub commit and the lock digest changed. The demo remains a synthetic, offline run that does not authorize anything, stays on HOLD and makes no production claim. One test that documented the replay-bundle validator accepting an unknown `bundle_version` no longer holds at the new replay-bundle pin, which refuses unknown versions by default (cogno-us/cognous-replay-bundle#71); see the pull request for that finding.

## What runs

Each step is the component's own public command, run in a subprocess with a minimal environment. `cognous-ocs` asserts every exit code and never imports component code.

| Layer | Command | Expected |
|---|---|---|
| declare | `aam validate`, `aam list-actions` on the manifest; `aam validate` on the bounded manifest | 0 |
| control | `acp run` for `crm_read` and `notes_search` (each then `acp validate-run` on its `run-record.json`) | 0 (applied) |
| control | `acp run` for `email_send`, then `acp validate-run` on its `run-record.json` | `run`: 1 (held, the expected blocking result); `validate-run`: 0 |
| replay | `arb validate`, `arb sign`, `arb verify`, `arb redact --targets`, `arb validate` on the redacted bundle | 0 |
| replay (negative controls) | `arb verify` with a wrong key; `arb verify` after editing the signed bundle | 1 |
| evidence | `agep validate`, `agep summarize`, `agep render` on the evidence pack | 0 |

### The control step

`acp run` builds a synthetic resolver from the bundled JSON files, runs the control plane's real bounded workflow (decide, effect-time revalidation, execute, reconcile) on a throwaway store, and writes a native `bounded-run-record.json`, a projection `run-record.json` that `acp validate-run` accepts, and a `result.json` envelope. The demo calls it three times with the same manifest, authority context and resolver state, one proposal each, always with `--now 2026-10-05T19:00:00Z` (the bundled grant and observation times are written for that instant) and fresh store and output directories under `control/<operation>/`.

- `crm_read` and `notes_search` are covered by the grant: exit 0, applied to the local file destination.
- `email_send` requests `outbound.send`. The requirement declares that scope; the grant does not cover it. This is the uncovered-permission pattern. The control plane holds the operation at decision time: exit 1 with the component's reason code `grant_scope_mismatch`, a `decisions` row with result `block`, and a `blocked_actions` row for the send. Nothing is applied. The demo treats exit 1 here as the expected result (it maps it to its own expectation, not to an `cognous-ocs` failure); under `--strict` the same decision propagates as exit 1 (`OCS_BLOCKING_FINDING`).

The evidence of the blocked send is that `blocked_actions` row: it names the `email_send` operation's action id, the same id the manifest declares and the proposal carries, and its `reason` is the component's reason code, passed through unchanged (also in the `acp run` envelope and the decision row). `cognous-ocs` does not remap or add reason codes. Each run's `acp run` output, including the codes, is in `logs/` and summarised unchanged under `extensions["x-control"]`.

### Replay and evidence

`acp run` writes fresh random decision and blocked ids every run, so the replay bundle and evidence pack cannot be fixed files that carry them. The bundled `replay-bundle.json` and `evidence-pack.json` are templates: `cognous-ocs` copies the real runs' action ids, decision ids, blocked id, times, reasons and reason codes into copies written to `outputs/` (`src/cognous_ocs/bind.py`), and the components then validate, sign, redact and render those copies. The reliance rows, inventories and narrative in the two documents stay synthetic and are not produced by the control plane.

Then `cognous-ocs` reads the produced artifacts and checks fourteen facts, for example: the manifest declares `email_send` as an external send needing authority; `acp run` applied the two reads and held the send with exit 1; nothing was applied for the send; a `blocked_actions` row names the `email_send` / `outbound.send` operation; the reason codes are the same in the envelope, decision and row; the replay bundle carries the same blocked id as the run record; target redaction removed the recipient; the evidence pack records the block with the component's reason code and lists the replay bundle; the HTML view shows the blocked send and its reason code. Facts and steps are in the JSON result under `extensions["x-facts"]` and `extensions["x-steps"]`.

## Exit codes

- 0: every step exited as expected and every fact held.
- 1: a step exited unexpectedly or a fact did not hold (`OCS_DEMO_EXPECTATION`), including a control plane that applies the send (`acp run` exit 0 where 1 is expected) or holds it for other reason codes; under `--strict`, also the blocked send itself (`OCS_BLOCKING_FINDING`), because `--strict` propagates `acp run`'s exit 1 for the send instead of the demo's expected-facts result.
- 2: output path refused (`OCS_PATHS`), or a component is missing or not at its pinned commit (`OCS_VERSION`).
- 4: a step could not run (timeout, OS error) or a bundled fixture failed its hash check (`OCS_INTERNAL`).

## Output directory

`DIR` receives `inputs/` (the bundled fixtures, hash-checked on load; the `acp run` inputs are under `inputs/acp-run/`), `control/<operation>/{store,out}` (the throwaway store and the `acp run` outputs), `outputs/` (the bound replay bundle and evidence pack, signed, redacted and tampered bundles, rendered markdown), `logs/` (one control-stripped log per step), `evidence.html`, and `demo-result.json`. A non-empty `DIR` is refused unless `--clean` is given, and `--clean` only replaces a directory that a previous demo marked as its own. On a nonzero exit `ci-diagnostics.json` is written into `DIR`.

## What it does not prove

- **Institutional authority.** The control plane's resolver is synthetic: grant, identity, status and policy are declared in bundled files and are not authenticated. The demo's inputs are the control plane's inputs; a hold or an apply here is the real workflow's decision on those inputs, not a statement about any institution.
- **A real-world effect.** `acp run` applies to a local file destination in a throwaway directory. No mail was sent, blocked or queued anywhere, and no real customer data was involved.
- **That `run-record.json` is more than a projection.** It is a `RunRecord` built from the native `bounded-run-record.json`; `acp validate-run` accepting it shows internal consistency only.
- **That the replay bundle and evidence pack came from the control plane.** They are synthetic templates re-keyed by `cognous-ocs` to the real runs' ids and reason codes.
- The replay signature uses a public demo-only key. It shows the signing and verification commands work and reject tampering. It says nothing about who produced a bundle.
- The pinned component versions may have defects, and newer versions may behave differently.
- Nothing about deployment or release status.

## Fixtures

`src/cognous_ocs/fixtures/customer-service/` and its `provenance.json`. The hub's `examples/customer-service-end-to-end` is narrative only (two markdown files), so there was nothing concrete to copy there. `manifest.json` is a byte copy of the action-manifest example. `run-record.json`, `replay-bundle.json` and `evidence-pack.json` are derived from the component examples and re-keyed so one synthetic run is consistent across the layers; the demo uses the last two as templates and no longer feeds the first one to the control step (`cognous-ocs init` and `cognous-ocs validate` still use it as a sample).

`acp-run/` holds the six inputs of the real control step: a bounded manifest (manifest 1.1, the form `acp run` binds proposals to; it declares `crm_read`, `notes_search` and `email_send` under the same names as `manifest.json`, which also declares `email_draft`), one authority context, one resolver state and one proposal per operation. They are generated by `tools/derive_fixtures.py`, which reimplements the scenario (including the control plane's canonical commitment for the manifest and payload digests) rather than copying the control plane's `examples/acp_run/` files; `provenance.json` records those example files, with hashes, as what the inputs were modelled on, records that none is a byte copy, and records the `--now` the times assume. `tools/derive_fixtures.py` reproduces everything from checkouts at the pinned commits. `provenance.json` records the hub commit and file hashes, each source commit, path and hash, the hash of every bundled file, and the control-plane pin against the hub lock; `make examples` fails if a bundled file differs from its recorded hash, and a test fails if the generator no longer reproduces the `acp-run/` inputs.
