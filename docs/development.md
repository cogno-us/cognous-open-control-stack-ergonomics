# Development gates

Everything here runs offline except installing the pinned component extra. Nothing publishes
anything: this repository has no upload target, and `make release-publish-check` fails while
`release-state.json` says publication is not authorized.

## Setup

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,stack]"
```

## Targets

| Target | What it does |
|---|---|
| `make check` | Fixed order: `lint`, `type`, `lock`, `tests`, `docs`, `claims`, `examples`, `schemas`, `actions`, `build`. Stops at the first failure. |
| `make lint` | `ruff check` and `ruff format --check`. |
| `make type` | `mypy` in strict mode over `src`, `tests` and `scripts`. |
| `make lock` | The `stack` extra pins equal `src/cognous_ocs/lock.py` (and any `requirements*.lock` file agrees). No lock files exist yet. |
| `make tests` | pytest with sockets disabled in-process and an 80% coverage floor (both set in `pyproject.toml`). |
| `make docs` | Local links and anchors resolve, every docs page is linked from the README, versions and the command and exit-code tables match the code. |
| `make claims` | The claim-boundary check (below). |
| `make examples` | Bundled example fixtures are byte-equal to the hashes in `provenance.json`; no stray or missing file. |
| `make schemas` | Every `schemas/vN/` snapshot matches the hashes in `schemas/SNAPSHOTS.json`; once a `v*` git tag exists, each snapshot directory present at the tag is byte-identical to it. See [schemas.md](schemas.md). |
| `make actions` | Third-party actions pinned by commit SHA with the release tag in a comment, no `*-latest` runner label, the composite action's inputs and upload paths, and its release version equal to the package version. See [action.md](action.md). |
| `make build` | Builds the wheel and sdist into `dist/`. |
| `make release-check` | `check`, then `wheel-check` (required and forbidden wheel members, the `cognous-ocs` console script and no `ocs` script, a `cognous_ocs-*` dist-info, claim scan of the shipped files) and `smoke` (clean virtualenv install of the wheel, `cognous-ocs --version` working and no `ocs` script installed, then the demo run under `unshare -n`). `smoke` fails if no network-namespace tool works; `OCS_SMOKE_ALLOW_NO_NETNS=1` runs it without isolation, never in CI. |
| `make release-publish-check` | Claim check, then `release-check`, then the authorization gate. The last step fails while `release-state.json` has `publication_authorized` or `production_authorized` false. Passing gates never authorizes publication. |

## Claim boundary

`release-state.json` records whether publication and production are authorized. While either is
false, `scripts/check_claim_boundaries.py` fails the build if any tracked text file, source string,
action text or built wheel member makes one of the three forbidden claims (the rules and their
variants, including hyphen and case changes and phrases split across lines, are defined in the
script; this page does not repeat the words, because the check scans it too). A missing or
malformed `release-state.json` counts as HOLD.

A match is excused only by an exact entry in `scripts/claim_boundaries_allowlist.json` (path,
rule, the whole line, and a reason). An entry that matches nothing fails the build. Only an owner
decision changes `release-state.json`.

## Pre-push hook

`.githooks/pre-push` runs `make pre-push` (lint and the claim check). It is not enabled
automatically. To enable it in your clone:

```
git config core.hooksPath .githooks
```
