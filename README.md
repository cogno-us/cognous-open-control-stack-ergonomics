# Cognous Open Control Stack: Ergonomics

Status: design phase (Phase 0). No code yet. Nothing here changes any Open Control Stack component.

This repository is the front door to the Open Control Stack: one command to install, try, check and wire into CI. It does none of the governing. The components still make every authority decision, refusal, replay and evidence record. If this repository disappeared, the stack would behave exactly as before.

## Rules this repository holds itself to

1. **One-way dependency.** It depends on the components only through their ODES contracts and public validators. No component depends on it.
2. **Read-only toward the stack.** Its only write path is its own scaffold (`init`). A differing existing file stops it; nothing is overwritten.
3. **No verdict authority.** It relays component verdicts and invents none. No local waivers or exceptions.
4. **Fails closed, runs offline.** An unknown or archival version is never a pass. Tests run with the network disabled.
5. **Claims are checked by code.** The build fails if a document says certified, compliant or production-ready while publication or production is on HOLD.
6. **Narrow surface.** The four layers (manifest, replay, evidence, plus the control layer they report on) and `validate`, `demo`, `init`, `doctor`, `ci`. No studies, no live adapters.

## Naming

Repository: `cognous-open-control-stack-ergonomics`. Command: `ocs` (subject to a package-name check before any publication). Publication is not authorized; nothing is released from this repository while the stack's publication decision is on HOLD.

## Contents

- [`docs/cli-contract.md`](docs/cli-contract.md): command set, exit codes, JSON result envelope, diagnostics file, reason-code naming. This is the Phase 0 deliverable.

## Phases

0. Design only: the CLI contract (this phase).
1. First proof: skeleton CLI, self-verifying customer-service demo, one-file HTML render.
2. Trust and diagnosis: `validate`, `init`, `doctor`, test defaults.
3. CI and release: composite action, wheel smoke test, schema snapshots.
4. Items that edit existing component repositories: only in a later freeze cycle.

Backlog and rationale: cogno-us/cognous-control-plane#42.

## License

Apache-2.0 (see `LICENSE`).
