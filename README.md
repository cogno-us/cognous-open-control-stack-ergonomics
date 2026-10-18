# Cognous Open Control Stack: Ergonomics

Status: Phases 1 to 3 are merged (offline demo and evidence render; validate, init, doctor, passthrough; composite action, schema snapshots, dashboard). Everything is `0.1.0-draft`. Nothing here changes any Open Control Stack component, and nothing is published from this repository.

This repository is the front door to the Open Control Stack: one command to install, try, check and wire into CI. It does none of the governing. The components still make every authority decision, refusal, replay and evidence record. If this repository disappeared, the stack would behave exactly as before.

## Rules this repository holds itself to

1. **One-way dependency.** It depends on the components only through their public CLI commands (`aam`, `acp`, `arb`, `agep`) at pinned commits, run in subprocesses. No component depends on it.
2. **Read-only toward the stack.** It writes only to output paths the user names (`--out`), never to component files; `init` is the only command that scaffolds into a workflow directory. A differing existing file stops `init`; nothing is overwritten.
3. **No verdict authority.** It relays component verdicts and invents none. No local waivers or exceptions.
4. **Fails closed, runs offline.** An unknown or archival version is never a pass. Tests run with the network disabled.
5. **Claims are checked by code.** The build fails if a document says certified, compliant or production-ready while publication or production is on HOLD.
6. **Narrow surface.** Passthrough for three layers (manifest, replay, evidence; no control-plane layer) and `validate`, `demo`, `init`, `doctor`, `evidence render`, `dashboard`, `schema export`, `ci`. No studies, no live adapters.

## Naming

Repository: `cognous-open-control-stack-ergonomics`. Distribution and command: `cognous-ocs` (the only installed command; there is no `ocs` script); Python import module: `cognous_ocs`. Publication is not authorized; nothing is released from this repository while the stack's publication decision is on HOLD.

## Quick start

```
python -m venv .venv && . .venv/bin/activate
pip install ".[stack]"     # the four components; the control plane is pinned ahead of the hub lock (docs/demo.md)
cognous-ocs --version
cognous-ocs demo customer-service --out demo-out   # offline; exit 0 only if the blocked email send is evidenced
cognous-ocs evidence render demo-out/outputs/evidence-pack.json --out view.html
```

The distribution is `cognous-ocs` (what `pip list` shows; wheel `cognous_ocs-<version>-py3-none-any.whl`); `cognous-ocs` is the only console script. The `stack` extra uses direct git references, so it is for installing from this repository, not for an index upload.

## Contents

- [`src/cognous_ocs/`](src/cognous_ocs): the package. `cognous_ocs.lock` holds the component pins; `cognous_ocs/fixtures/customer-service/` holds the bundled example and its `provenance.json`.
- [`docs/cli-contract.md`](docs/cli-contract.md): command set, exit codes, JSON result envelope, diagnostics file, reason-code naming. This is the Phase 0 deliverable.
- [`docs/demo.md`](docs/demo.md): the offline customer-service demo, its steps, exit codes and limits.
- [`docs/validate.md`](docs/validate.md): `cognous-ocs validate`, the (kind, version) registry, exit mapping and what a pass does not prove.
- [`docs/init-doctor.md`](docs/init-doctor.md): the workflow scaffold and the read-only setup diagnosis.
- [`docs/development.md`](docs/development.md): `make check`, `make release-check`, the claim-boundary check and the opt-in pre-push hook.
- [`docs/testing.md`](docs/testing.md): test defaults (network off, coverage floor, strict types) and the lock per extra.
- [`docs/schemas.md`](docs/schemas.md): `cognous-ocs schema export` and the schema snapshots for the result envelope and diagnostics file.
- [`docs/action.md`](docs/action.md): the composite GitHub Action, `cognous-ocs ci`, and what the action guarantees.
- [`docs/evidence-render.md`](docs/evidence-render.md): what `cognous-ocs evidence render` shows and what it does not prove.
- [`docs/dashboard.md`](docs/dashboard.md): `cognous-ocs dashboard`, a read-only static HTML reading of verification reports, mutation reports, packs and run records.

## Phases

0. Design only: the CLI contract (this phase).
1. First proof: skeleton CLI, self-verifying customer-service demo, one-file HTML render.
2. Trust and diagnosis: `validate`, `init`, `doctor`, test defaults.
3. CI and release: composite action, wheel smoke test, schema snapshots.
4. Items that edit existing component repositories: only in a later freeze cycle.

Backlog and rationale: cogno-us/cognous-control-plane#42.

## License

Apache-2.0 (see `LICENSE`).
