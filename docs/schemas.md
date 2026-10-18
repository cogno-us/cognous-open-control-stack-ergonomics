# Schema snapshots and `ocs schema export`

```
ocs schema export --out DIR [--format json]
```

Writes the schema snapshots bundled with this release into `DIR`: `SNAPSHOTS.json` and one directory per snapshot (`v1/`). `DIR` must resolve inside the workspace or the temp directory. The command never replaces a differing file: it checks every target first and exits 2 (`OCS_PATHS`) without writing anything if one differs. Re-running with identical bytes changes nothing, and a partial export resumes by creating only the missing files. Before writing, every bundled file is checked against its recorded sha256; a damaged bundle exits 4 (`OCS_INTERNAL`).

## What is in `v1/`

| File | Describes |
|---|---|
| `envelope.schema.json` | The result object `ocs --format json` prints (cli-contract section 4), `envelope_version` `1`. |
| `diagnostics.schema.json` | `ci-diagnostics.json` (cli-contract section 5). |

These describe what `ocs` itself emits. Their `$id` urns (`urn:ocs:schema:...`) name the tool, not the distribution, so renaming the distribution to `cognous-ocs` did not touch any snapshot. Component artifact schemas belong to the components; the (kind, version) registry behind `ocs validate` lives in `src/ocs/registry.py` and is not copied here. A schema is a shape, not a verdict, and every contract is still a draft: the snapshots are named by `envelope_version`, nothing is tagged or released.

## Where the files live

- Source: `schemas/vN/` and `schemas/SNAPSHOTS.json` in the repository (hand-maintained JSON, no second copy in code).
- Wheel: `ocs/schema_resources/` (hatch `force-include`). A source checkout falls back to `schemas/`.
- An `unreleased/` directory may exist as an exporter smoke target; it is never listed, checked or bundled, and `make release-check` fails if it appears in the wheel.

## Immutability check

`make schemas` (`scripts/check_schema_snapshots.py`) fails when:

- a `schemas/vN/` directory is not listed in `schemas/SNAPSHOTS.json`, a listed one is missing, or any file differs from its recorded hash or is added or removed;
- a `v*` git tag exists and a `schemas/vN/` file present at that tag differs now, or was deleted. Editing the manifest cannot excuse this: the tag is the reference. There are no tags yet, so today the manifest is the only reference; the tag comparison is covered by tests against a temporary repository.

To change a snapshot after it is recorded, add `schemas/v2/` and run `python scripts/check_schema_snapshots.py --record v2`. `--record` refuses to touch a snapshot that is already recorded. The tests validate real `ocs` output (pass, invalid and error results, and a written diagnostics file) against these schemas, so a change to the envelope without a new snapshot fails the build.
