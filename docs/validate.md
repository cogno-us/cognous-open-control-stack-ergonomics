# `ocs validate`

```
ocs validate PATH --kind {manifest,run-record,replay-bundle,evidence-pack} [--format json]
```

Checks one artifact by running the component's own public validator in a subprocess. `ocs` never imports component code and adds no verdict of its own.

## How the check is chosen

The `--kind` argument and the version parsed from the file select one registry entry. Nothing else in the file selects a check: a missing `artifact_kind` cannot bypass it, and a present one that disagrees with `--kind` is refused.

| Kind | Version member | Registered versions | Component command |
|---|---|---|---|
| `manifest` | `manifest_version` | `1.0` full, `1.1` full | `aam validate` |
| `run-record` | none in the pinned control plane | `unversioned` full | `acp validate-run` |
| `replay-bundle` | `bundle_version` | `0.1` full, `0.2.0` archival-only | `arb validate` |
| `evidence-pack` | `pack_version` | `0.1` full | `agep validate` |

- `full`: a public component command checks the artifact semantically.
- `archival-only`: known, but no public command validates it semantically; always exit 2, never a pass. (`arb validate` is version-blind and checks the 0.1 shape, so it must not be pointed at a `0.2.0` reconstruction bundle.)
- Any other pair, including a missing, non-string or ambiguous version, fails closed with `OCS_KIND_UNREGISTERED`.
- A run record has no version member, so its one registered version is the literal `unversioned`. If a run record declares a version-like member (`run_record_version`, `record_version`, `schema_version`, `version`, `artifact_version`), that value must register, and two such members are ambiguous.

The component validators do not check versions themselves (`arb validate` accepts a bundle declaring `bundle_version` `9.9.9`; `agep validate` has no version check), which is why the registry decides first.

## Exit codes

| Exit | When | Reason code |
|---|---|---|
| 0 | the component validator exited 0 | none |
| 2 | unregistered pair | `OCS_KIND_UNREGISTERED` |
| 2 | archival-only pair | `OCS_ARCHIVAL_ONLY` |
| 2 | not one strict JSON object, `artifact_kind` disagrees, or the component rejected the artifact (its exit 1 or 2) | `OCS_INPUT_INVALID` |
| 2 | component missing or not at its pinned commit | `OCS_VERSION` |
| 2 | path refused | `OCS_PATHS` |
| 4 | the component crashed, timed out, or exited with an undocumented code | `OCS_INTERNAL` |

## What a pass does not prove

A pass says `artifact-internal validation passed`. It does not prove external source linkage (that the artifact matches what really happened), producer authenticity (who made it), completeness or truth of the content, or anything beyond the pinned component validator's own checks. Component warnings still exit 0; they appear in `extensions["x-component-output"]`.
