"""`cognous-ocs evidence render`: one self-contained HTML view of an evidence pack.

The page has inline CSS and no scripts, fonts, images or links. Every value taken from the
pack is stripped of control characters and HTML-escaped. The view shows what the pack says;
it does not validate the pack and decides nothing.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cognous_ocs import __version__
from cognous_ocs.envelope import strip_controls

MAX_PACK_BYTES = 5_000_000
REGISTERED_PACK_VERSIONS = frozenset({"0.1"})
LIST_MEMBERS = ("action_inventory", "blocked_actions", "reliance_summary", "replay_bundles")

DOES_NOT_PROVE = (
    "that the pack is structurally valid (this view does not run the evidence pack validator)",
    "that the pack was produced by the stated producer, or that its records are authentic",
    "that the actions listed as allowed or blocked happened, or happened as described",
    "that the evidence references resolve to anything",
    "anything about actions the pack does not list",
    "that a blocked action stayed blocked outside the recorded run",
)


class RenderRefused(Exception):
    """The pack cannot be rendered; carries the reason code to report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Pack:
    data: dict[str, Any]
    declared_version: str


def load_pack(path: Path) -> Pack:
    """Read and structurally check a pack. Fails closed on an unregistered version."""
    from cognous_ocs.envelope import OCS_INPUT_INVALID, OCS_KIND_UNREGISTERED

    if path.stat().st_size > MAX_PACK_BYTES:
        raise RenderRefused(OCS_INPUT_INVALID, "evidence pack is larger than the 5 MB limit")
    try:
        data = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RenderRefused(
            OCS_INPUT_INVALID, f"evidence pack is not valid JSON ({exc.__class__.__name__})"
        ) from exc
    if not isinstance(data, dict):
        raise RenderRefused(OCS_INPUT_INVALID, "evidence pack is not a JSON object")
    version = data.get("pack_version")
    if not isinstance(version, str):
        raise RenderRefused(OCS_INPUT_INVALID, "evidence pack has no pack_version")
    if version not in REGISTERED_PACK_VERSIONS:
        raise RenderRefused(
            OCS_KIND_UNREGISTERED,
            f"evidence-pack version {strip_controls(version)[:40]} is not a registered kind/version pair",
        )
    if not isinstance(data.get("pack_id"), str):
        raise RenderRefused(OCS_INPUT_INVALID, "evidence pack has no pack_id")
    for member in LIST_MEMBERS:
        items = data.get(member, [])
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise RenderRefused(OCS_INPUT_INVALID, f"{member} must be a list of objects")
    return Pack(data, version)


def _e(value: object) -> str:
    return html.escape(strip_controls("" if value is None else value), quote=True)


def _flag(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return _e(value)


def _table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_e(empty)}</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def classify(pack: dict[str, Any]) -> dict[str, str]:
    """Per declared action: blocked, allowed, or declared (no outcome recorded in the pack)."""
    blocked = {str(b.get("action_name")) for b in pack.get("blocked_actions", [])}
    status: dict[str, str] = {}
    for action in pack.get("action_inventory", []):
        name = str(action.get("action_name"))
        if name in blocked:
            status[name] = "blocked"
        elif action.get("default_posture") == "allowed":
            status[name] = "allowed"
        else:
            status[name] = "declared"
    return status


_CSS = """
:root{--bg:#fbfaf7;--fg:#1c1b19;--muted:#5f5b53;--line:#d9d4c8;--card:#fff;--ok:#1d6b3a;--okbg:#e4f3e9;--bad:#a3261c;--badbg:#fbe6e3;--note:#7a5b00;--notebg:#fbf2d6}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ece9e1;--muted:#a49f93;--line:#3a3833;--card:#1f1e1b;--ok:#7fd49b;--okbg:#18301f;--bad:#f2968d;--badbg:#3a1a17;--note:#e6c869;--notebg:#34290b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:64rem;margin:0 auto;padding:1.5rem 16px 3rem}
h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.1rem;margin:2rem 0 .5rem}
.meta{color:var(--muted);font-size:.9rem;margin:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:1rem;margin:.75rem 0}
.blocked{border-left:6px solid var(--bad)}
.blocked h3{margin:0 0 .25rem;font-size:1.05rem}
.chip{display:inline-block;border-radius:999px;padding:.05rem .6rem;font-size:.8rem;font-weight:600;white-space:nowrap}
.chip.blocked{background:var(--badbg);color:var(--bad)}.chip.allowed{background:var(--okbg);color:var(--ok)}.chip.declared{background:var(--notebg);color:var(--note)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{border-bottom:1px solid var(--line);padding:.4rem .6rem;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted)}
.empty{color:var(--muted)}
.boundary{background:var(--notebg);border:1px solid var(--line);border-radius:8px;padding:.75rem 1rem}
.boundary ul{margin:.25rem 0 0;padding-left:1.25rem}
code{font:.9em ui-monospace,Menlo,Consolas,monospace}
footer{margin-top:2rem;color:var(--muted);font-size:.85rem}
"""


def render_html(pack: dict[str, Any]) -> str:
    status = classify(pack)
    actions = pack.get("action_inventory", [])
    blocked = pack.get("blocked_actions", [])
    reliance = pack.get("reliance_summary", [])
    bundles = pack.get("replay_bundles", [])

    declared_rows = [
        [
            f"<code>{_e(a.get('action_name'))}</code>",
            _e(a.get("tool_name")),
            _e(a.get("action_type")),
            _flag(a.get("authority_required")),
            _flag(a.get("review_required")),
            _e(a.get("default_posture")),
            f'<span class="chip {status[str(a.get("action_name"))]}">{status[str(a.get("action_name"))]}</span>',
        ]
        for a in actions
    ]
    allowed_rows = [
        [
            f"<code>{_e(a.get('action_name'))}</code>",
            _e(a.get("tool_name")),
            _e(a.get("description")),
        ]
        for a in actions
        if status[str(a.get("action_name"))] == "allowed"
    ]
    blocked_cards = (
        "".join(
            '<div class="card blocked">'
            f'<h3><span class="chip blocked">blocked</span> <code>{_e(b.get("action_name"))}</code>'
            f" ({_e(b.get('action_type'))} via {_e(b.get('tool_name'))})</h3>"
            f"<p><strong>Reason:</strong> {_e(b.get('reason_summary'))}</p>"
            f'<p class="meta">Recorded blocks: {_e(b.get("count"))} · evidence reference: '
            f"<code>{_e(b.get('evidence_reference'))}</code></p></div>"
            for b in blocked
        )
        or '<p class="empty">The pack records no blocked actions.</p>'
    )
    reliance_rows = [
        [
            _e(r.get("source_name")),
            _e(r.get("source_type")),
            _e(r.get("count")),
            _e(r.get("scope_summary")),
            f"<code>{_e(r.get('evidence_reference'))}</code>",
        ]
        for r in reliance
    ]
    bundle_rows = [
        [
            f"<code>{_e(b.get('bundle_id'))}</code>",
            _e(b.get("run_id")),
            _e(b.get("status")),
            _flag(b.get("signed")),
            _flag(b.get("redacted")),
            _e(b.get("validation_status")),
            f"<code>{_e(b.get('evidence_reference'))}</code>",
        ]
        for b in bundles
    ]
    boundary = "".join(f"<li>{_e(item)}</li>" for item in DOES_NOT_PROVE)
    title = _e(pack.get("title") or pack.get("pack_id"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Evidence view: {title}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>{title}</h1>
<p class="meta">Pack <code>{_e(pack.get("pack_id"))}</code> · pack version {_e(pack.get("pack_version"))} · generated {_e(pack.get("generated_at"))} · review status {_e(pack.get("review_status"))}</p>

<h2>Declared actions</h2>
{_table(["Action", "Tool", "Type", "Authority required", "Review required", "Default posture", "Recorded outcome"], declared_rows, "The pack declares no actions.")}

<h2>Allowed</h2>
{_table(["Action", "Tool", "Description"], allowed_rows, "No declared action is recorded as allowed.")}

<h2>Blocked, with reason</h2>
{blocked_cards}

<h2>Reliance</h2>
{_table(["Source", "Type", "Count", "Scope", "Evidence reference"], reliance_rows, "The pack records no reliance sources.")}

<h2>Replay inventory</h2>
{_table(["Bundle", "Run", "Status", "Signed", "Redacted", "Validation", "Evidence reference"], bundle_rows, "The pack lists no replay bundles.")}

<h2>What this view does not prove</h2>
<div class="boundary"><ul>{boundary}</ul></div>

<footer>Rendered by cognous-ocs {_e(__version__)} from the pack as given. Offline, self-contained, no scripts. This page relays what the pack records and decides nothing.</footer>
</main>
</body>
</html>
"""
