"""Subcommand registration. Each command module adds itself here."""

from __future__ import annotations

import argparse
from typing import Any


def register(sub: argparse._SubParsersAction[Any], common: argparse.ArgumentParser) -> None:
    """Add subcommands (none yet in the skeleton)."""
