"""Shared test helpers: fake component commands and a pin lookup."""

from __future__ import annotations

import stat
from pathlib import Path

from ocs.lock import COMPONENTS


def pinned(distribution: str) -> str | None:
    return {c.distribution: c.sha for c in COMPONENTS.values()}.get(distribution)


def fake_bin(root: Path, bodies: dict[str, str] | None = None) -> Path:
    directory = root / "fakebin"
    directory.mkdir(exist_ok=True)
    for comp in COMPONENTS.values():
        script = directory / comp.command
        script.write_text((bodies or {}).get(comp.command, "#!/bin/sh\nexit 0\n"))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return directory
