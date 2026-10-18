# The customer-service demo

```
ocs demo customer-service --out DIR [--clean] [--strict]
```

Runs the bundled customer-service example end to end, offline, and exits 0 only if the blocked email send is evidenced in every layer. It needs no network and no key. The components are installed with `pip install ".[stack]"`, which pins them to the commits in the hub's `component-lock.json`.

## What runs

Each step is the component's own public command, run in a subprocess with a minimal environment. `ocs` asserts every exit code and never imports component code.

| Layer | Command | Expected |
|---|---|---|
| declare | `aam validate`, `aam list-actions` on the manifest | 0 |
| control | `acp validate-run` on the run record | 0 |
| replay | `arb validate`, `arb sign`, `arb verify`, `arb redact --targets`, `arb validate` on the redacted bundle | 0 |
| replay (negative controls) | `arb verify` with a wrong key; `arb verify` after editing the signed bundle | 1 |
| evidence | `agep validate`, `agep summarize`, `agep render` on the evidence pack | 0 |

Then `ocs` reads the accepted artifacts and checks eleven facts, for example: the manifest declares `email_send` as an external send needing authority; the run record allows `crm_read` and `notes_search` and blocks `email_send` (never allows it) with a reason that names the missing authority; the replay bundle carries the same blocked send as the run record; target redaction removed the recipient; the evidence pack records the block and lists the replay bundle; the HTML view shows the blocked send and its reason. Facts and steps are in the JSON result under `extensions["x-facts"]` and `extensions["x-steps"]`.

## Exit codes

- 0: every step exited as expected and every fact held.
- 1: a step exited unexpectedly or a fact did not hold (`OCS_DEMO_EXPECTATION`); under `--strict`, also the blocked send itself (`OCS_BLOCKING_FINDING`), because `--strict` propagates the control plane's blocking decision instead of the demo's expected-facts result.
- 2: output path refused (`OCS_PATHS`), or a component is missing or not at its pinned commit (`OCS_VERSION`).
- 4: a step could not run (timeout, OS error) or a bundled fixture failed its hash check (`OCS_INTERNAL`).

## Output directory

`DIR` receives `inputs/` (the bundled fixtures, hash-checked on load), `outputs/` (signed, redacted and tampered bundles, rendered markdown), `logs/` (one control-stripped log per step), `evidence.html`, and `demo-result.json`. A non-empty `DIR` is refused unless `--clean` is given, and `--clean` only replaces a directory that a previous demo marked as its own. On a nonzero exit `ci-diagnostics.json` is written into `DIR`.

## What it does not prove

- **The control plane did not run.** There is no control-plane command that executes a run; `acp` only validates and redacts exported records. The run record is a bundled synthetic fixture, so the control step shows that the control plane's validator accepts a record in which the send was blocked, not that the control plane made that decision here.
- No real agent action was blocked and no real customer data was involved. The fixtures are synthetic.
- The replay signature uses a public demo-only key. It shows the signing and verification commands work and reject tampering. It says nothing about who produced a bundle.
- The pinned component versions may have defects, and newer versions may behave differently.
- Nothing about deployment or release status.

## Fixtures

`src/ocs/fixtures/customer-service/` and its `provenance.json`. The hub's `examples/customer-service-end-to-end` is narrative only (two markdown files), so there was nothing concrete to copy there. `manifest.json` is a byte copy of the action-manifest example. The other three are derived from the component examples and re-keyed so one synthetic run is consistent across the four layers. `tools/derive_fixtures.py` reproduces them from checkouts at the pinned commits. `provenance.json` records the hub commit and file hashes, each source commit, path and hash, and the hash of every bundled file; a test fails if a bundled file differs from its recorded hash.
