# Evidence render: what it shows and what it does not prove

`ocs evidence render PATH --out FILE` reads one evidence pack (JSON, pack version `0.1`) and writes one self-contained HTML file.

## What it does

- Shows the pack's declared actions, the ones recorded as allowed, each blocked action with its reason, the reliance summary and the replay inventory.
- Inline CSS only. No scripts, no fonts, no images, no links, no network. A content security policy in the page forbids everything else.
- Every value from the pack has control characters stripped and is HTML-escaped before it is placed in the page.
- Refuses an unregistered pack version (exit 2, `OCS_KIND_UNREGISTERED`), malformed input (exit 2, `OCS_INPUT_INVALID`), an output outside the workspace or temp directory, a symlinked or hard-linked output, and an output that aliases the input (exit 2, `OCS_PATHS`).

## What it does not prove

The page repeats this list. Rendering a pack does not show:

- That the pack is structurally valid. The command does not run the evidence pack validator; `agep validate` does that, and the demo runs it first.
- That the pack came from the stated producer, or that its records are authentic. Nothing is signed or checked here.
- That the actions listed as allowed or blocked happened, or happened as described.
- That the evidence references (for example `run-record.json#blocked-...`) resolve to anything.
- Anything about actions the pack does not list.
- That a blocked action stayed blocked outside the recorded run.

"Recorded outcome" on the page is read from the pack: an action is `blocked` when the pack's `blocked_actions` names it, `allowed` when its default posture is `allowed`, and `declared` otherwise (no outcome recorded). The view produces no verdict of its own, which is why the JSON result carries no `verdict_source`.
