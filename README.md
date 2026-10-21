# Cognous Open Control Stack: Ergonomics

Status: Phases 1 to 3 are merged (offline demo and evidence render; validate, init, doctor, passthrough; composite action, schema snapshots, dashboard). Everything is `0.1.0-draft`. Nothing here changes any Open Control Stack component, and nothing is published from this repository.

This repository is the front door to the Open Control Stack: one command to install, try, check and wire into CI. It does none of the governing. The components still make every authority decision, refusal, replay and evidence record. If this repository disappeared, the stack would behave exactly as before.

## At a glance

| | |
|---|---|
| What it is | One command line, `cognous-ocs`: an offline demo, an evidence view, artifact checks, a workflow scaffold, a setup diagnosis, a static dashboard and a CI entry that wraps a composite GitHub Action. |
| Who it is for | Someone trying the stack for the first time, and anyone wiring its checks into CI. |
| Status label | **merged**. Not in the hub component lock; contract label `0.1.0-draft`; publication is not authorized. |
| What it does not do | It decides nothing, grants nothing and adds no verdict. It relays what the components decide. It does not drive the ODES tooling, the verifier, the exchange or the runtime. |
| Requirements | Linux, Python 3.11 or later, and GitHub access for the `stack` extra (it installs four components from pinned git references). |

## Where it fits

<!-- stack-map:start -->
<!-- Canonical block: identical text in every component README. Source: docs/stack-map.md in the hub. Refresh at the pin round. -->
The Open Control Stack groups its repositories by responsibility. The groups are a navigation map, not an execution sequence and not a requirement to install every component. The hub [component lock](https://github.com/cogno-us/cognous-open-control-stack/blob/main/component-lock.json) is the only source of selected revisions; [release status](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/release-status.md) states their evidence and limits. The component revisions selected for the stack are listed in the hub's `component-lock.json`; this repository does not record which hub commit it was last checked against.

| Group | Members |
| --- | --- |
| **01 Knowledge & Provenance** | [Evidence Registry (The Index)](https://github.com/cogno-us/cognous-evidence-registry) · [Evidence Attestation (BitRep)](https://github.com/cogno-us/cognous-evidence-attestation) |
| **02 Institutional Governance** | [Institutional Governance (Alvorada)](https://github.com/cogno-us/cognous-institutional-governance) |
| **03 Declaration & Policy Control** | [Action Manifest](https://github.com/cogno-us/cognous-action-manifest) · [Control Plane](https://github.com/cogno-us/cognous-control-plane) |
| **04 Constrained Execution** | [Execution Runtime](https://github.com/cogno-us/cognous-execution-runtime) |
| **05 Replay & Evidence** | [Replay Bundle](https://github.com/cogno-us/cognous-replay-bundle) · [Governance Evidence Pack](https://github.com/cogno-us/cognous-governance-evidence-pack) · [ODES](https://github.com/cogno-us/open-decision-evidence-standard) |
| **Cross-cutting exchange** | [Governed Exchange (GAX/IMX)](https://github.com/cogno-us/cognous-governed-exchange) |
| **Tooling and qualification** (side group) | [Ergonomics (`cognous-ocs`)](https://github.com/cogno-us/cognous-open-control-stack-ergonomics) · [Verifier](https://github.com/cogno-us/cognous-open-control-stack-verifier) · [Orchestrator](https://github.com/cogno-us/cognous-stack-orchestrator) · [Qualification](https://github.com/cogno-us/cognous-stack-qualification) |

Status labels used in this README: **merged** (reviewed and merged in the owning repository; not by itself selected), **selected** (pinned in the hub lock), **known limit** (a limitation that is stated and kept visible) and **proposed** (draft or planned work, not a supported interface). Full map, names and commands: [stack map](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/stack-map.md), [naming](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/naming.md), [profiles and tools](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/profiles-and-tools.md). A signature, registry entry, message receipt or evidence summary does not grant execution authority.
<!-- stack-map:end -->

This repository is in the **Tooling and qualification** side group. It drives four components through their public commands only, in subprocesses, at pinned commits: Action Manifest (`aam`), Control Plane (`acp`), Replay Bundle (`arb`) and Governance Evidence Pack (`agep`). It does not drive ODES, the Verifier, Governed Exchange or the Execution Runtime. Shared limits are stated once in the hub [guarantees and limits](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/guarantees-and-limits.md).

## Rules this repository holds itself to

1. **One-way dependency.** It depends on the components only through their public CLI commands (`aam`, `acp`, `arb`, `agep`) at pinned commits, run in subprocesses. No component depends on it.
2. **Read-only toward the stack.** It writes only to output paths the user names (`--out`), never to component files; `init` is the only command that scaffolds into a workflow directory. A differing existing file stops `init`; nothing is overwritten.
3. **No verdict authority.** It relays component verdicts and invents none. No local waivers or exceptions.
4. **Fails closed, runs offline.** An unknown or archival version is never a pass. Tests run with the network disabled.
5. **Claims are checked by code.** The build fails if a document says certified, compliant or production-ready while publication or production is on HOLD.
6. **Narrow surface.** Passthrough for three layers (manifest, replay, evidence; no control-plane layer) and `validate`, `demo`, `init`, `doctor`, `evidence render`, `dashboard`, `schema export`, `ci`. No studies, no live adapters.

## Naming

Repository: `cognous-open-control-stack-ergonomics`. Distribution and command: `cognous-ocs` (the only installed command; there is no `ocs` script); Python import module: `cognous_ocs`. Publication is not authorized; nothing is released from this repository while the stack's publication decision is on HOLD. All names across the stack are collected in the hub [naming](https://github.com/cogno-us/cognous-open-control-stack/blob/main/docs/naming.md) page.

## Quick start

Verified from a clean checkout in a scratch virtual environment. It needs network access to GitHub once, for the `stack` extra; after that the demo runs offline.

```
git clone https://github.com/cogno-us/cognous-open-control-stack-ergonomics
cd cognous-open-control-stack-ergonomics
python -m venv .venv && . .venv/bin/activate
pip install ".[stack]"     # the four components, at the revisions in the hub lock (see "Pins and the hub lock")
cognous-ocs --version
cognous-ocs demo customer-service --out demo-out   # offline; exit 0 only if the blocked email send is evidenced
cognous-ocs evidence render demo-out/outputs/evidence-pack.json --out view.html
```

Expected output. `--version` prints the CLI, contract, example and pinned-component lines and exits 0:

```text
cognous-ocs 0.1.0
cli-contract 0.1.0
bundled-example customer-service 0.2.0
bundled-component aam cogno-us/cognous-action-manifest@fa170815ef96
bundled-component acp cogno-us/cognous-control-plane@e77fe0ee8714
bundled-component arb cogno-us/cognous-replay-bundle@53c0cd60d565
bundled-component agep cogno-us/cognous-governance-evidence-pack@99ba3211450b
```

`demo` exits 0 and its first lines read:

```text
demo: pass (exit 0)
  the blocked email_send is evidenced in all four layers (19 component commands run offline, every exit code as expected); acp run held it: grant_scope_mismatch; view: evidence.html
  verdict source: control-plane (bounded-authorization-effect 0.1.0)
```

It then prints a long `does not prove` line (synthetic resolver, no real-world effect, synthetic replay and pack templates, demo-only signing key). `evidence render` exits 0 with `evidence render: pass (exit 0)` and writes one self-contained HTML file (about 5 KB); it states that the pack was not validated by that command. The demo took about 7 seconds on an idle machine and under 30 seconds on a loaded one.

If you installed without the `stack` extra, the commands fail closed: `demo` exits 2 with reason code `OCS_VERSION` and the line `Install with: pip install 'cognous-ocs[stack]' (from this repository)`. `--version` still works. That is the intended behaviour (an unknown or missing component is never a pass), not a bug.

What this does not prove: a passing demo shows that the real pinned components, run on synthetic inputs, block the email send and evidence it in all layers. It does not show that any real action was blocked, that the grant came from an institution, or anything about deployment (see [the demo page](docs/demo.md#what-it-does-not-prove)).

The distribution is `cognous-ocs` (what `pip list` shows; wheel `cognous_ocs-<version>-py3-none-any.whl`); `cognous-ocs` is the only console script. The `stack` extra uses direct git references, so it is for installing from this repository, not for an index upload.

## Commands

Every registered subcommand is in the [CLI contract](docs/cli-contract.md#2-commands) table; the same list here. Global options: `--format text|json`, `--strict`, `--out`, `--no-color`. Exit codes 0 to 4 are defined in the [contract](docs/cli-contract.md#3-exit-codes).

| Command | Purpose | Writes | Page | Label |
|---|---|---|---|---|
| `cognous-ocs --version` | Print CLI, distribution, contract and bundled-component versions. | nothing | [cli-contract](docs/cli-contract.md) | merged |
| `cognous-ocs demo customer-service --out DIR [--clean] [--strict]` | Run the bundled offline example; exit 0 only if the blocked email send is evidenced. | `DIR` | [demo](docs/demo.md) | merged |
| `cognous-ocs validate PATH --kind KIND` | Check one artifact with the component's own validator. Kinds: `manifest`, `run-record`, `replay-bundle`, `evidence-pack`. | nothing | [validate](docs/validate.md) | merged |
| `cognous-ocs init workflow --out DIR` | Scaffold a manifest, a policy file and a sample run record. | `DIR` | [init and doctor](docs/init-doctor.md) | merged |
| `cognous-ocs doctor [--workflow DIR]` | Read-only setup diagnosis. | nothing | [init and doctor](docs/init-doctor.md) | merged |
| `cognous-ocs evidence render PATH --out FILE` | One self-contained HTML view of an evidence pack; it does not validate the pack. | `FILE` | [evidence render](docs/evidence-render.md) | merged |
| `cognous-ocs dashboard --out FILE INPUT...` | Read-only static HTML reading of reports, packs and run records; adds no verdict. | `FILE` | [dashboard](docs/dashboard.md) | merged |
| `cognous-ocs schema export --out DIR` | Export the schema snapshots bundled with this release. | `DIR` | [schemas](docs/schemas.md) | merged |
| `cognous-ocs ci` | Entry used by the composite action: validate, gate, write diagnostics. | declared output paths | [action](docs/action.md) | merged |
| `cognous-ocs manifest ...`, `replay ...`, `evidence ...` | Thin passthrough to `aam`, `arb` and `agep` (no control-plane layer). | as the component does | [cli-contract](docs/cli-contract.md#2-commands) | merged |

## What ships

| Item | What it is | Label |
|---|---|---|
| `src/cognous_ocs/` | The package (import module `cognous_ocs`): commands, registry, runner, render, dashboard. No runtime dependency. | merged |
| `src/cognous_ocs/fixtures/customer-service/` | The bundled example and its `provenance.json`. | merged |
| `.github/actions/ocs/` | The composite GitHub Action that runs `cognous-ocs ci` ([action](docs/action.md)). Not published: reference it by commit SHA. | merged |
| `schemas/` | Schema snapshots for the result envelope and the diagnostics file, with recorded hashes ([schemas](docs/schemas.md)). | merged |
| `examples/sample-repo/` | The workflow this repository's CI runs the action on. | merged |
| `requirements/` | Hashed locks for the `dev` and `stack` extras ([testing](docs/testing.md)). | merged |
| `scripts/` | Repository checks: docs alignment, claim boundaries, lock freshness, examples parity, schema snapshots, action hygiene, wheel contents, release authorization. What they do and do not check, including that none reads the hub lock: [development](docs/development.md#what-is-and-is-not-checked-2026-10-22). | merged |
| `tools/derive_fixtures.py` | Regenerates the control-step demo inputs from checkouts at the pinned commits. | merged |
| `release-state.json` | The hold record: publication and production are not authorized. | known limit |

## Pins and the hub lock

The four component pins are in `src/cognous_ocs/lock.py` and in the `stack` extra. Each equals the revision listed for that component in the hub `component-lock.json`; the table below shows the comparison. `acp run`, which the demo needs, exists at the pinned control-plane revision ([demo page](docs/demo.md#pins-and-the-hub-lock)).

<!-- pins-table:start -->
<!-- Generated from src/cognous_ocs/lock.py and the hub component-lock.json. Refresh at the pin round: rerun the generator and replace this table; do not hand-copy. -->

| Layer | Command | Lock key | Pinned here | Hub lock | Same |
|---|---|---|---|---|---|
| declare | `aam` | `action_manifest` | `fa170815ef96` | `fa170815ef96` | yes |
| control | `acp` | `control_plane` | `e77fe0ee8714` | `e77fe0ee8714` | yes |
| replay | `arb` | `replay_bundle` | `53c0cd60d565` | `53c0cd60d565` | yes |
| evidence | `agep` | `governance_evidence_pack` | `99ba3211450b` | `99ba3211450b` | yes |

<!-- pins-table:end -->

## Known limits

- **known limit** `demo` runs against a synthetic resolver and a local file destination; a pass is not evidence of an institutional grant or a real-world effect.
- **known limit** The `stack` extra needs GitHub access (direct git references). Without it the commands fail closed with `OCS_VERSION`.
- **known limit** `evidence render` and `dashboard` display what they are given and add no verdict; a pass from `validate` means artifact-internal validation by the component, nothing more.
- **known limit** No control-plane passthrough layer exists; only the demo calls `acp run`.

## Tests

```
pip install -e ".[dev,stack]"
make check        # lint, strict types, lock freshness, tests (network off, 80% coverage floor), docs alignment, claim boundary, examples parity, schema snapshots, action hygiene, build
```

Details are in [development](docs/development.md) and [testing](docs/testing.md).

## Documentation index

Every page under `docs/` is linked below; the list is the documentation index for this repository.

### Contents

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
