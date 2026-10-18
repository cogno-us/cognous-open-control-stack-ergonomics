"""Clean-virtualenv install smoke test.

Installs the built wheel (with the pinned `stack` extra, which needs network at install time
only) into a fresh venv, then runs `cognous-ocs --version` (no `ocs` script may exist) and the customer-service demo with the
network namespace removed (`unshare -n`). Exit 0 within the time limit and a blocked send in
the rendered HTML are required. If no network-namespace tool works the smoke test FAILS;
set OCS_SMOKE_ALLOW_NO_NETNS=1 to run without isolation (never in CI).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIMIT_SECONDS = 120
BLOCKED = 'chip blocked">blocked</span> <code>email_send</code>'


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def netns_prefix() -> list[str] | None:
    for prefix in (["unshare", "-n"], ["sudo", "-n", "unshare", "-n"]):
        if shutil.which(prefix[0]) and run([*prefix, "true"]).returncode == 0:
            return prefix
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", type=Path, help="wheel file, or a directory holding one wheel")
    ap.add_argument("--work", type=Path, default=None)
    args = ap.parse_args(argv)
    wheels = sorted(args.path.glob("*.whl")) if args.path.is_dir() else [args.path]
    if len(wheels) != 1:
        print(f"expected exactly one wheel, found {len(wheels)}")
        return 1
    work = args.work or Path(tempfile.mkdtemp(prefix="ocs-smoke-"))
    work.mkdir(parents=True, exist_ok=True)
    venv = work / "venv"
    if venv.exists():
        shutil.rmtree(venv)
    steps = [
        [sys.executable, "-m", "venv", str(venv)],
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "-q",
            f"{wheels[0].resolve()}[stack]",
        ],
    ]
    for cmd in steps:
        done = run(cmd)
        if done.returncode != 0:
            print(f"smoke: setup failed: {' '.join(cmd[:4])}\n{done.stderr[-2000:]}")
            return 1
    ocs = str(venv / "bin" / "cognous-ocs")
    if (venv / "bin" / "ocs").exists():
        print("smoke: an `ocs` script was installed; the only command is cognous-ocs")
        return 1
    version = run([ocs, "--version"])
    if version.returncode != 0 or not version.stdout.startswith("cognous-ocs "):
        print("smoke: `cognous-ocs --version` failed")
        return 1
    schemas_out = work / "schemas"
    if schemas_out.exists():
        shutil.rmtree(schemas_out)
    exported = run([ocs, "schema", "export", "--out", str(schemas_out)])
    if exported.returncode != 0 or not (schemas_out / "v1" / "envelope.schema.json").is_file():
        print(f"smoke: `cognous-ocs schema export` from the wheel failed\n{exported.stdout[-800:]}")
        return 1
    prefix = netns_prefix()
    if prefix is None:
        if os.environ.get("OCS_SMOKE_ALLOW_NO_NETNS") != "1":
            print(
                "smoke: no working `unshare -n` (also tried sudo -n); refusing to run the demo with network"
            )
            return 1
        prefix = []
        print("smoke: WARNING running without network isolation (OCS_SMOKE_ALLOW_NO_NETNS=1)")
    out = work / "demo"
    started = time.monotonic()
    done = run(
        [
            *prefix,
            "timeout",
            str(LIMIT_SECONDS),
            ocs,
            "demo",
            "customer-service",
            "--out",
            str(out),
            "--clean",
        ]
    )
    elapsed = time.monotonic() - started
    if done.returncode != 0:
        print(f"smoke: demo exited {done.returncode}\n{done.stdout[-1500:]}{done.stderr[-1500:]}")
        return 1
    html = out / "evidence.html"
    if not html.is_file() or BLOCKED not in html.read_text(encoding="utf-8"):
        print("smoke: evidence.html does not show the blocked email send")
        return 1
    print(
        f"smoke: ok (demo exit 0 in {elapsed:.1f}s, {'isolated' if prefix else 'NOT isolated'}; {' '.join(prefix) or 'no netns'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
