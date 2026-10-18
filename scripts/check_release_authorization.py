"""Final gate of `make release-publish-check`: passing gates never authorizes publication.

Exits 1 while release-state.json says publication_authorized is false (or is missing or
malformed). It does not publish anything and there is no publish target in this repository.
Only an owner decision, recorded by editing release-state.json, can change the answer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repo  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    root = ap.parse_args(argv).root
    try:
        state = _repo.read_release_state(root)
    except _repo.StateError as exc:
        print(f"release-publish-check: refused: {exc} (treated as HOLD)")
        return 1
    if not state["publication_authorized"]:
        print(
            "release-publish-check: refused: release-state.json publication_authorized is false.\n"
            "Every gate before this one may have passed; passing gates never authorizes publication."
        )
        return 1
    if not state["production_authorized"]:
        print(
            "release-publish-check: refused: production_authorized is false (production on HOLD)."
        )
        return 1
    print(
        "release-publish-check: release-state.json authorizes publication; this repository still publishes nothing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
