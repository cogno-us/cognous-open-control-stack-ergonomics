"""Upload gate for the cognous-ocs composite action. Standard library only.

The action uploads from the output directory only when that directory carries the marker of THIS
run: `ci-summary.json` must be a regular file whose `extensions["x-run"]["nonce"]` equals the nonce
`confine_inputs.py` generated for this invocation. Anything else (the CLI never ran, an install
failed, an earlier run's output, a file planted by an earlier step or by the caller, a refusal that
published no summary) publishes nothing: `publish=false`. Files are never trusted because of their
name. Messages are fixed text; no input value is echoed.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path

OWNED = {"ci-summary.json", "ci-summary.md", "ci-diagnostics.json", "reports", "evidence", "inputs"}
NONCE = re.compile(r"^[0-9a-f]{16,64}$")
MAX_SUMMARY = 16 * 1024 * 1024


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def current_run(out: Path, nonce: str) -> bool:
    """True only if `out` holds a summary written by the run that owns `nonce`, and only owned,
    link-free content."""
    if not NONCE.match(nonce) or not out.is_dir() or out.is_symlink():
        return False
    summary = out / "ci-summary.json"
    if not _regular(summary) or summary.stat().st_size > MAX_SUMMARY:
        return False
    try:
        body = json.loads(summary.read_text(encoding="utf-8"))
        found = body["extensions"]["x-run"]["nonce"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not isinstance(found, str) or not hmac.compare_digest(found.encode(), nonce.encode()):
        return False
    for child in out.iterdir():
        if child.name not in OWNED or child.is_symlink():
            return False
        if child.is_dir():
            for sub in child.rglob("*"):
                if sub.is_symlink() or not (sub.is_dir() or _regular(sub)):
                    return False
    return True


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    raw = env.get("OCS_OUT_DIR", "")
    ok = bool(raw) and current_run(Path(raw), env.get("OCS_RUN_NONCE", ""))
    line = f"publish={'true' if ok else 'false'}\n"
    target = env.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        sys.stdout.write(line)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if ok and summary:
        md = Path(raw) / "ci-summary.md"
        if _regular(md):
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(md.read_text(encoding="utf-8"))
    if not ok:
        sys.stderr.write(
            "::notice title=cognous-ocs action::no output from this run was found, nothing is uploaded\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
