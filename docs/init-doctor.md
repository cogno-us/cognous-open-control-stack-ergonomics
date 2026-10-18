# `cognous-ocs init workflow` and `cognous-ocs doctor`

## `cognous-ocs init workflow --out DIR`

Writes four synthetic starter files into `DIR` (created if missing; `DIR` must resolve inside the workspace or the temp directory, no symlinks):

| File | What it is |
|---|---|
| `manifest.json` | an action manifest (version 1.0) with a read action and an external send that needs authority; passes `aam validate` |
| `policy.json` | the control plane's frame lists: `allowed_tools`, `blocked_tools`, `policy_version` |
| `run-record.json` | a sample run record with the read allowed and the send blocked; passes `acp validate-run` |
| `workflow.json` | names the three files above, plus `replay_signing` and `redaction` settings that `doctor` reads |

`workflow.json` is the fourth file because signing and redaction settings need a home that is not the manifest or the control plane's records.

Rules:
- Bytes are deterministic. Running again with identical bytes changes nothing (same content, mtime and inode).
- A partial scaffold resumes: only missing files are created, using exclusive create (never truncate).
- Any managed file that exists and differs, or is a symlink, a directory or hard-linked, stops the command with exit 2 `OCS_PATHS` before anything is written, including the missing files. Nothing is replaced; you move or delete the file yourself.
- Every name, scope and role in the files is a placeholder. The scaffold does not prove the workflow is right.

## `cognous-ocs doctor [--workflow DIR]`

Strictly read-only: it never runs the agent or a component, never writes (not even `ci-diagnostics.json`), and never opens a socket. A test snapshots the directory tree, mtimes and bytes before and after, with writes, process spawns and sockets made to raise. It reads files and the process environment only. It runs no validator; use `cognous-ocs validate` for that.

| Code | PASS when | Without `--workflow` |
|---|---|---|
| `OCS_VERSION` | all four components are installed at the commits pinned in `cognous_ocs.lock` (the control plane is ahead of the hub lock, see `docs/demo.md`) | runs |
| `OCS_MANIFEST` | manifest parses, its version is fully registered, every action has a type, and every non-read action has an authority requirement | SKIP |
| `OCS_POLICY_BIND` | every manifest action is in exactly one of `allowed_tools` or `blocked_tools` | SKIP |
| `OCS_PATHS` | the workflow's file references stay inside the directory (no absolute, `..`, symlink, CR/LF/NUL) | SKIP |
| `OCS_SIGNING` | `replay_signing` is `{"mode": "demo-only"}`, or `secret-env` naming a variable that is set (the value is never printed) | SKIP |
| `OCS_REDACTION` | `redaction.profile` names one of `targets`, `targets-and-output`, `targets-output-reasons` | SKIP |

Each check reports PASS, FAIL or SKIP with its code, a short message and one action (`extensions["x-checks"]` in JSON). A check whose input is unusable because of an earlier failure is SKIP, naming the code to fix first. Exit 0 when nothing FAILs, exit 2 when anything does (outcome `invalid`, `reason_codes` lists the failing codes).

A pass says only that the pieces are present and consistent. It does not prove any artifact validates, that the workflow is safe or authorised, or anything about producers.
