# The ocs composite action and `ocs ci`

`.github/actions/ocs/action.yml` is a composite action that installs the pinned CLI, runs `ocs ci` on a workflow, writes reports and diagnostics, and uploads a minimal set of files. It relays component verdicts and decides nothing. This repository's CI runs it on `examples/sample-repo/`.

```yaml
- uses: actions/checkout@<sha>   # pin by commit SHA, tag in a comment
- uses: actions/setup-python@<sha>
  with: {python-version: "3.12"}
- uses: <owner>/cognous-open-control-stack-ergonomics/.github/actions/ocs@<sha>
  with:
    workflow: path/to/workflow
    evidence-pack: path/to/evidence-pack.json
```

Nothing is published from this repository, so there is no released action reference yet; use a commit SHA of a checkout you control, or `./.github/actions/ocs` in this repository.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `manifest` | none | Action manifest file to validate. |
| `workflow` | none | Workflow directory (see `ocs init workflow`): runs the setup consistency checks, then validates its manifest and run record. |
| `evidence-pack` | none | The strict evidence input: the pack to validate. Choose this or `non-assurance`, never both. |
| `non-assurance` | `false` | Explicit non-assurance mode. No pack is checked, so the outcome is `review`, never `pass`. |
| `strict` | `false` | `true` makes a `review` outcome exit 1. |
| `out-dir` | `ocs-out` | Output directory, inside the workspace or the runner temp directory. |
| `report-mode` | `full` | `full` runs every check; `fail-fast` stops at the first one that does not pass and marks the rest `not_run`. |
| `upload-reports` | `true` | Upload the minimal set (below). |
| `upload-full-artifacts` | `false` | Also upload the whole output directory, including copies of the raw manifest and run record. |
| `retention-days` | `14` | Artifact retention, 1 to 90. |
| `upload-name` | `ocs-reports` | Artifact name; the full upload appends `-full`. |
| `install` | `source` | `source` installs the `ocs` in the action's own checkout with the pinned component extra; `preinstalled` uses the `ocs` on `PATH`. |

Outputs: `exit-code` (the exit code of `ocs ci`) and `out-dir`. The action step itself exits with that code.

## What it guarantees

- **Version binding.** The action carries `release-version.txt`. `ocs ci --expect-version` compares it with the installed CLI, and a mismatch exits 2 with `OCS_VERSION` before anything else runs, whichever `install` mode was used. `scripts/check_action.py` fails the build if the file differs from the package version.
- **Fail-closed profile.** It refuses (exit 2, `OCS_INPUT_INVALID`) unless exactly one of `evidence-pack` or `non-assurance: true` is given. A check in the confinement helper runs this before any install, and `ocs ci` repeats it.
- **Path confinement.** `confine_inputs.py` (standard library only) runs first. It exits 2 (`OCS_PATHS`) if an input contains CR, LF or NUL; if `out-dir` is outside the workspace and the runner temp directory, is one of those roots, goes through a symlink, or contains upload-glob characters; if the output directory holds files `ocs ci` does not own; or if an input is missing, lies inside the output directory, or contains it. Messages are fixed text and never echo an input. Inputs reach scripts only through `env:`, never by interpolation into shell. `ocs ci` re-checks with `ocs.paths`, and a test runs both implementations on the same cases.
- **Minimal upload.** By default only `ci-summary.json`, `ci-summary.md`, `ci-diagnostics.json`, `evidence/` and `reports/` are uploaded, named one by one. The evidence directory holds a copy of the pack only when it validated. Raw inputs are copied (to `inputs/`) and uploaded only with `upload-full-artifacts: true`.
- **Pinned third-party actions.** `actions/upload-artifact` is referenced by commit SHA with its release tag in a comment; `scripts/check_action.py` checks every workflow and action file, and rejects `*-latest` runner labels.

## `ocs ci`

```
ocs ci --out DIR (--evidence-pack PATH | --non-assurance) [--manifest PATH] [--workflow DIR]
       [--report-mode full|fail-fast] [--expect-version V] [--copy-inputs] [--strict] [--format json]
```

Steps, in order: the doctor consistency checks on `--workflow`, validation of `--manifest`, the workflow's manifest and run record, then the evidence pack, each through `ocs validate`. The overall outcome is the worst step: `error` over `invalid` over `fail` over `review`. With an evidence pack and every step passing it is `pass`; in non-assurance mode it is `review` at best. `verdict_source` names the first step that did not pass and has one, else the last step that has one; every step is listed under `extensions["x-steps"]`.

Output under `--out`: `ci-summary.json` (the envelope), `ci-summary.md`, `reports/NN-<kind>.json` (one result per step that ran), `evidence/evidence-pack.json` (only if the pack validated), `inputs/` (only with `--copy-inputs`), and `ci-diagnostics.json` on any nonzero exit. An existing output directory may hold only these names; each run replaces them.

A pass says every listed artifact passed its pinned component validator. It does not prove that the evidence matches what really happened, that a run record came from the control plane, that a workflow is authorised to run, or anything about deployment or release status.

## Tests

`tests/test_ci.py` and `tests/test_action.py` cover the profile, version binding, confinement (including a parity test against `ocs.paths`), gating and the files written. The workflow `.github/workflows/ci.yml` runs the action on the sample repository (job `action-sample`) and runs negative jobs that must fail: a version mismatch exits 2, a missing profile is refused, and an output path outside the workspace is refused (job `action-negative`).
