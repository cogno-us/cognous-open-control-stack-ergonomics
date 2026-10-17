"""Path and write confinement (cli-contract section 8).

Outputs must resolve inside the workspace (the current directory) or the runner
temp directory. Symlink and hard-link aliasing is refused. Input paths containing
CR or LF are refused. No input may alias an owned output.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class PathRefused(Exception):
    """A path was refused; the CLI maps this to exit 2 with OCS_PATHS."""


def allowed_roots(workspace: Path | None = None) -> list[Path]:
    """Lexical roots outputs may live under: the workspace, the temp dir, RUNNER_TEMP."""
    roots = [
        Path(os.path.normpath(workspace or Path.cwd())),
        Path(os.path.normpath(tempfile.gettempdir())),
    ]
    runner = os.environ.get("RUNNER_TEMP")
    if runner:
        roots.append(Path(os.path.normpath(runner)))
    return roots


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def check_input(raw: str) -> Path:
    """Validate an input path string; the file must exist and be a regular file."""
    if "\r" in raw or "\n" in raw or "\x00" in raw:
        raise PathRefused("input path contains a CR, LF or NUL character")
    path = Path(raw)
    if not path.is_file():
        raise PathRefused("input path is not a readable regular file")
    return path


def confine_output(raw: str, *, workspace: Path | None = None) -> Path:
    """Return the absolute output path, or raise PathRefused.

    Refuses: CR/LF/NUL in the string, a target outside the allowed roots, a target
    equal to a root itself, any symlink between the root and the target, and an
    existing file with more than one hard link.
    """
    if "\r" in raw or "\n" in raw or "\x00" in raw:
        raise PathRefused("output path contains a CR, LF or NUL character")
    base = Path(os.path.normpath(workspace or Path.cwd()))
    candidate = Path(raw)
    absolute = Path(os.path.normpath(candidate if candidate.is_absolute() else base / candidate))
    for root in allowed_roots(workspace):
        if not _inside(absolute, root):
            continue
        if absolute == root:
            raise PathRefused("output path is a workspace or temp root itself")
        walker = root
        for part in absolute.relative_to(root).parts:
            walker = walker / part
            if walker.is_symlink():
                raise PathRefused("output path involves a symlink")
        if not _inside(absolute.resolve(), root.resolve()):
            raise PathRefused("output path resolves outside its root")
        if absolute.is_file() and absolute.stat().st_nlink > 1:
            raise PathRefused("output file has more than one hard link")
        return absolute
    raise PathRefused("output path is outside the workspace and the temp directory")


def refuse_alias(inputs: list[Path], outputs: list[Path]) -> None:
    """No input may alias an owned output (same file or same location)."""
    for inp in inputs:
        for out in outputs:
            if inp.resolve() == out.resolve():
                raise PathRefused("an input aliases an owned output")
            if out.exists() and os.path.samefile(inp, out):
                raise PathRefused("an input aliases an owned output")
