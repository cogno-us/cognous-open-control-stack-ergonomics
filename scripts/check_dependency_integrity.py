"""Dependency integrity policy (csf_fd18fac96a9c68c8d48ec390), checked by code.

Nothing that CI, the composite action, make, or the repository scripts install may be resolved
outside the committed locks. The checker is deterministic, offline, and standard-library plus PyYAML.

1. Every `pip install` / `uv pip install` (workflows, the composite action, the Makefile, scripts/,
   tools/, and the test suite's argv lists) must take one of the allowed lock-consuming forms:
   - `pip install --require-hashes --no-deps --no-build-isolation -r requirements/<name>.txt`
     for a hashed PyPI lock;
   - `pip install --no-deps --no-build-isolation -r requirements/<name>-vcs.txt` for the first-party
     SHA-pinned components (pip cannot hash VCS requirements, so they are a separate step);
   - `pip install --no-deps --no-build-isolation [-e] <local path | wheel>` for this repository itself
     (no extras: dependencies come from the locks), after a lock that holds the build toolchain;
   - offline (`--no-index`) installs of a local path.
   Any other pip/uv command that resolves or fetches (download, wheel, uv sync/add/run/tool, pipx, curl,
   wget, npm, ...), `python -m build` without `--no-isolation`, an index/find-links/trusted-host option
   or environment variable, `-U`, `--pre`, and constraint files are refused.
2. Hashed locks: every requirement is `name==version` with only well-formed, non-placeholder sha256
   hashes, versions inside the pyproject ranges (see _lockfile.py).
3. VCS pins: full 40-hex SHA, repository in the allow-list (the Cognous lock's components), exactly the
   Cognous lock's commits, installed `--no-deps`.
4. Build backend: pyproject's build requirements are satisfied by an exactly locked version in
   requirements/build.txt, which holds hatchling, editables, setuptools and wheel with the same
   versions as requirements/dev.txt; every build runs without build isolation.
5. GitHub Actions: every `uses:` is a local path, a `docker://` image with a sha256 digest, or
   `owner/repo[/path]@<40 hex>`; container images carry digests.
6. No alternate package indexes: no pip.conf / uv.toml / .pypirc, no `[tool.uv]` / `[tool.pip]`, no
   PIP_* / UV_* environment settings other than harmless ones.

The networked regeneration of the locks is a documented local process (docs/testing.md), not a CI job.
"""

from __future__ import annotations

import argparse
import ast
import re
import shlex
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lockfile  # noqa: E402
import _repo  # noqa: E402

FULL_SHA = re.compile(r"[0-9a-f]{40}")
GIT_URL = re.compile(r"git\+[A-Za-z0-9+.-]*://[^\s\"'),\]]+")
STRICT_GIT = re.compile(
    r"git\+https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@([0-9a-f]{40})"
)
ACTION_REF = re.compile(r"[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
INDEX_FILES = {"pip.conf", "pip.ini", "uv.toml", ".uv.toml", ".pypirc", "poetry.toml", "Pipfile"}
SAFE_ENV = {"PIP_DISABLE_PIP_VERSION_CHECK", "PIP_NO_CACHE_DIR", "PIP_NO_INPUT"}
ENV_SETTING = re.compile(r"\b((?:PIP|UV)_[A-Z0-9_]+)\s*[:=]")
LOCK_FILE = re.compile(r"(?:\$\{?root\}?/)?(?:\./)?requirements/([A-Za-z0-9._-]+)\.txt")
BAD_FLAGS = {
    "-i": "an alternate index",
    "--index-url": "an alternate index",
    "--extra-index-url": "an extra index",
    "-f": "a find-links source",
    "--find-links": "a find-links source",
    "--trusted-host": "a trusted host",
    "-U": "an upgrade",
    "--upgrade": "an upgrade",
    "--pre": "pre-releases",
    "-c": "a constraints file",
    "--constraint": "a constraints file",
    "--no-verify-hashes": "disabling hash checks",
    "--proxy": "a proxy",
    "--cert": "a certificate override",
    "--client-cert": "a certificate override",
    "--use-feature": "an experimental feature",
    "--no-binary": "building from source",
    "--break-system-packages": "an unmanaged environment",
    "--target": "an unmanaged target",
    "--user": "an unmanaged target",
}
GOOD_FLAGS = {
    "--require-hashes",
    "--no-deps",
    "--no-build-isolation",
    "-q",
    "-qq",
    "--quiet",
    "--no-index",
    "--disable-pip-version-check",
    "--no-cache-dir",
    "--only-binary=:all:",
    "-e",
    "--editable",
}
READONLY_PIP = {"list", "freeze", "check", "show", "uninstall", "--version", "-V"}
UNLOCKED_TOOLS = {
    "pipx": "pipx resolves outside the locks",
    "easy_install": "easy_install resolves outside the locks",
    "poetry": "poetry resolves outside the locks",
    "conda": "conda resolves outside the locks",
    "mamba": "mamba resolves outside the locks",
    "npm": "npm is not locked here",
    "npx": "npx is not locked here",
    "yarn": "yarn is not locked here",
    "pnpm": "pnpm is not locked here",
    "curl": "curl fetches unpinned content",
    "wget": "wget fetches unpinned content",
    "apt": "apt installs unpinned packages",
    "apt-get": "apt-get installs unpinned packages",
    "brew": "brew installs unpinned packages",
    "cargo": "cargo is not locked here",
    "gem": "gem is not locked here",
}
WRAPPERS = {"sudo", "env", "command", "time", "exec", "nohup", "!", "nice", "timeout"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
SHELLS = {"bash", "sh", "zsh", "dash"}
TOOLCHAIN_LOCKS = {"build", "dev"}


@dataclass
class Command:
    """One shell statement or argv list, as the policy sees it."""

    where: str  # file:line
    unit: str  # ordering scope: job, action, file
    words: list[str]


def _norm_lines(text: str) -> list[str]:
    """Logical shell lines: heredoc bodies dropped, backslash continuations joined."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    skip_to: str | None = None
    for line in lines:
        if skip_to is not None:
            if line.strip() == skip_to:
                skip_to = None
            continue
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if m:
            skip_to = m[2]
            line = line[: m.start()]
        out.append(line)
    joined: list[str] = []
    buf = ""
    for line in out:
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
        else:
            joined.append(buf + line)
            buf = ""
    if buf:
        joined.append(buf)
    return joined


def _shell_statements(text: str) -> Iterator[tuple[int, list[str]]]:
    """(line number, words) for every simple command; redirections are dropped."""
    pending = ""
    start = 0
    for number, raw in enumerate(_norm_lines(text), 1):
        line = pending + ("\n" if pending else "") + raw
        if not pending:
            start = number
        if not line.strip() or line.lstrip().startswith("#"):
            pending = ""
            continue
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            lex.commenters = "#"
            tokens = list(lex)
        except ValueError:
            pending = line  # an open quote continues on the next line
            continue
        pending = ""
        current: list[str] = []
        skip_next = False
        for tok in tokens:
            if skip_next:
                skip_next = False
                continue
            if tok and set(tok) <= set("<>&|;()"):
                if "<" in tok or ">" in tok:
                    skip_next = True  # redirection target or file descriptor
                    continue
                if current:
                    yield start, current
                current = []
                continue
            current.append(tok)
        if current:
            yield start, current
    if pending.strip():
        yield start, ["<unparseable>", pending.strip()[:60]]


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _is_pip(word: str) -> bool:
    return re.fullmatch(r"pip(?:3(?:\.\d+)?)?", _base(word)) is not None


def _is_python(word: str) -> bool:
    return re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", _base(word)) is not None


def _strip_prefix(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and (
        words[i] in WRAPPERS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i])
    ):
        i += 1
    return words[i:]


def analyze_words(where: str, words: list[str], ctx: Context) -> None:
    """Apply the install policy to one command (already split into words)."""
    for word in words:
        m = ENV_SETTING.match(word)
        if m and m[1] not in SAFE_ENV:
            ctx.error(f"{where}: {m[1]} changes package resolution; not allowed")
    words = _strip_prefix(words)
    if not words:
        return
    head = _base(words[0])
    if words[0] == "<unparseable>":
        if re.search(r"\b(pip|uv)\b", words[1]):
            ctx.error(f"{where}: cannot parse a command that mentions pip/uv ({words[1]!r})")
        return
    if head in SHELLS and "-c" in words:
        idx = words.index("-c")
        if idx + 1 < len(words):
            for _, inner in _shell_statements(words[idx + 1]):
                analyze_words(where, inner, ctx)
        return
    if head in UNLOCKED_TOOLS:
        ctx.error(f"{where}: `{head}`: {UNLOCKED_TOOLS[head]}")
        return
    if head in {"xargs", "find"} and any(_is_pip(w) or _base(w) == "uv" for w in words[1:]):
        ctx.error(f"{where}: an installer run through `{head}` cannot be checked")
        return
    if _is_pip(words[0]):
        analyze_pip(where, words[1:], ctx)
    elif _is_python(words[0]) and words[1:2] == ["-m"] and len(words) > 2:
        module = words[2]
        if module == "pip":
            analyze_pip(where, words[3:], ctx)
        elif module == "build":
            if not {"--no-isolation", "-n"} & set(words[3:]):
                ctx.error(
                    f"{where}: `python -m build` must run with --no-isolation (locked toolchain)"
                )
        elif module in {"pipx", "uv", "easy_install", "ensurepip"}:
            ctx.error(f"{where}: `python -m {module}` is not an allowed installer")
    elif head == "uv":
        sub = words[1:2]
        if sub == ["pip"] and words[2:3] == ["install"]:
            analyze_pip(where, words[2:], ctx, tool="uv pip")
        elif sub in (["--version"], ["-V"]):
            return
        else:
            ctx.error(f"{where}: `uv {' '.join(words[1:3])}` resolves outside the committed locks")
    elif head == "setup.py" or (len(words) > 1 and words[1].endswith("setup.py")):
        ctx.error(f"{where}: setup.py invocations are not allowed")


def analyze_pip(where: str, args: list[str], ctx: Context, tool: str = "pip") -> None:
    sub_at = next((i for i, a in enumerate(args) if not a.startswith("-")), None)
    for a in args[: sub_at if sub_at is not None else len(args)]:
        if a in {"--version", "-V"}:
            return
        if a not in {"-q", "--quiet", "--disable-pip-version-check", "--no-cache-dir"}:
            ctx.error(f"{where}: `{tool}` global option {a} is not allowed")
    if sub_at is None:
        return
    sub = args[sub_at]
    rest = args[sub_at + 1 :]
    if sub in READONLY_PIP:
        return
    if sub != "install":
        ctx.error(f"{where}: `{tool} {sub}` is not allowed; only lock-consuming installs are")
        return
    flags: set[str] = set()
    positionals: list[str] = []
    reqfiles: list[str] = []
    i = 0
    ok = True
    while i < len(rest):
        a = rest[i]
        if a in {"-r", "--requirement"}:
            if i + 1 >= len(rest):
                ctx.error(f"{where}: {a} lacks a file")
                return
            reqfiles.append(rest[i + 1])
            i += 2
            continue
        if a.startswith("--requirement="):
            reqfiles.append(a.split("=", 1)[1])
        elif a in BAD_FLAGS or a.split("=", 1)[0] in BAD_FLAGS:
            ctx.error(
                f"{where}: {a.split('=', 1)[0]} ({BAD_FLAGS[a.split('=', 1)[0]]}) is not allowed"
            )
            ok = False
        elif a.startswith("-") and a in GOOD_FLAGS:
            flags.add(a)
        elif a.startswith("-"):
            ctx.error(f"{where}: pip option {a} is not an allowed lock-consuming form")
            ok = False
        else:
            positionals.append(a)
        i += 1
    if not ok:
        return
    if reqfiles:
        analyze_requirement_install(where, flags, positionals, reqfiles, ctx)
    else:
        analyze_local_install(where, flags, positionals, ctx)


def analyze_requirement_install(
    where: str, flags: set[str], positionals: list[str], reqfiles: list[str], ctx: Context
) -> None:
    if positionals or {"-e", "--editable"} & flags:
        ctx.error(f"{where}: a lock install may not also name packages or editable paths")
        return
    if len(reqfiles) != 1:
        ctx.error(f"{where}: exactly one -r requirements/<name>.txt per install")
        return
    m = LOCK_FILE.fullmatch(reqfiles[0])
    if m is None:
        ctx.error(f"{where}: -r {reqfiles[0]} is not a committed requirements/<name>.txt lock")
        return
    name = m[1]
    if not (ctx.root / "requirements" / f"{name}.txt").is_file():
        ctx.error(f"{where}: requirements/{name}.txt does not exist")
        return
    vcs = name.endswith("-vcs")
    need = {"--no-deps", "--no-build-isolation"} | (set() if vcs else {"--require-hashes"})
    missing = sorted(need - flags)
    if missing:
        ctx.error(f"{where}: install of requirements/{name}.txt lacks {' '.join(missing)}")
    if vcs and "--require-hashes" in flags:
        ctx.error(f"{where}: --require-hashes cannot be used with VCS pins (pip refuses them)")
    if vcs:
        ctx.vcs_files_installed.add(name)
        ctx.require_toolchain(where)
    elif name in TOOLCHAIN_LOCKS:
        ctx.toolchain_seen()
    else:
        ctx.hashed_files_installed.add(name)


def analyze_local_install(
    where: str, flags: set[str], positionals: list[str], ctx: Context
) -> None:
    if len(positionals) != 1:
        ctx.error(f"{where}: pip install must name either -r <lock> or exactly one local path")
        return
    target = positionals[0]
    offline = "--no-index" in flags
    local = (
        target in {".", ".."}
        or target.startswith(("/", "./", "../", "$", "~"))
        or "/" in target
        or target.endswith(".whl")
    )
    if not local or "://" in target or "git+" in target or "[" in target or "==" in target:
        ctx.error(
            f"{where}: install target {target!r} must be a local path or wheel without extras; "
            "dependencies come from the locks"
        )
        return
    if not offline:
        missing = sorted({"--no-deps", "--no-build-isolation"} - flags)
        if missing:
            ctx.error(f"{where}: local install lacks {' '.join(missing)}")
        ctx.require_toolchain(where)


class Context:
    def __init__(self, root: Path, errors: list[str]) -> None:
        self.root = root
        self._errors = errors
        self.unit = ""
        self.vcs_files_installed: set[str] = set()
        self.hashed_files_installed: set[str] = set()
        self._toolchain = False

    def error(self, message: str) -> None:
        if message not in self._errors:
            self._errors.append(message)

    def begin_unit(self, unit: str) -> None:
        self.unit = unit
        self._toolchain = False

    def toolchain_seen(self) -> None:
        self._toolchain = True

    def require_toolchain(self, where: str) -> None:
        if not self._toolchain:
            self.error(
                f"{where}: a --no-build-isolation install needs the locked build toolchain "
                "(-r requirements/build.txt or dev.txt) installed first in the same job/script"
            )


# ---------------------------------------------------------------- sources


def _walk_yaml(node: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, str(key), value
            yield from _walk_yaml(value, f"{path}/{key}")
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _walk_yaml(value, f"{path}[{idx}]")


def _yaml_steps(doc: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    """(unit, step) in order for workflow jobs and composite actions."""
    if not isinstance(doc, dict):
        return
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job_name, job in jobs.items():
            if isinstance(job, dict):
                for step in job.get("steps") or []:
                    if isinstance(step, dict):
                        yield f"job:{job_name}", step
    runs = doc.get("runs")
    if isinstance(runs, dict):
        for step in runs.get("steps") or []:
            if isinstance(step, dict):
                yield "action", step


def check_github_files(root: Path, ctx: Context) -> None:
    files = sorted((root / ".github").rglob("*.yml")) + sorted((root / ".github").rglob("*.yaml"))
    for path in files:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            ctx.error(f"{rel}: not valid YAML ({type(exc).__name__})")
            continue
        for _where, key, value in _walk_yaml(doc):
            if key == "uses" and isinstance(value, str):
                check_uses(rel, value, ctx)
            if key == "image" and isinstance(value, str) and not DIGEST.search(value):
                ctx.error(f"{rel}: container image {value!r} has no @sha256 digest")
            if key == "container" and isinstance(value, str) and not DIGEST.search(value):
                ctx.error(f"{rel}: container image {value!r} has no @sha256 digest")
            if key == "env" and isinstance(value, dict):
                for name in value:
                    if re.fullmatch(r"(?:PIP|UV)_[A-Z0-9_]+", str(name)) and name not in SAFE_ENV:
                        ctx.error(f"{rel}: env {name} changes package resolution; not allowed")
        current = ""
        for unit, step in _yaml_steps(doc):
            if unit != current:
                ctx.begin_unit(f"{rel}:{unit}")
                current = unit
            run = step.get("run")
            if isinstance(run, str):
                for line_no, words in _shell_statements(run):
                    analyze_words(
                        f"{rel} ({unit}, step {step.get('name') or step.get('id') or '?'}, line {line_no})",
                        words,
                        ctx,
                    )
                for match in ENV_SETTING.finditer(run):
                    if match[1] not in SAFE_ENV:
                        ctx.error(
                            f"{rel}: `{match[1]}` set in a run script changes package resolution"
                        )


def check_uses(rel: str, ref: str, ctx: Context) -> None:
    if ref.startswith("./"):
        return
    if ref.startswith("docker://"):
        if not DIGEST.search(ref):
            ctx.error(f"{rel}: `uses: {ref}` is a floating docker image; pin @sha256:<digest>")
        return
    if not ACTION_REF.fullmatch(ref):
        ctx.error(f"{rel}: `uses: {ref}` is not pinned by a 40-hex commit SHA")


def check_makefile(root: Path, ctx: Context) -> None:
    path = root / "Makefile"
    if not path.is_file():
        return
    ctx.begin_unit("Makefile")
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("\t"):
            text = re.sub(r"\$\((?:PY|PYTHON)\)", "python", line.lstrip("\t@-"))
            for _, words in _shell_statements(text):
                analyze_words(f"Makefile:{number}", words, ctx)
    # make recipes run in the caller's environment; build is checked, installs are refused
    # unless in an allowed form.


def _argv_literal(node: ast.AST) -> list[str] | None:
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    words: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            words.append(elt.value)
        elif isinstance(elt, ast.JoinedStr):
            words.append(
                "".join(
                    v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "$dyn"
                    for v in elt.values
                )
            )
        else:
            words.append("$dyn")
    return words


def check_python_argv(root: Path, ctx: Context) -> None:
    """argv lists in scripts/, tools/ and tests/ that run pip."""
    for base in ("scripts", "tools", "tests"):
        for path in sorted((root / base).rglob("*.py")):
            if path.name in {"check_dependency_integrity.py", "test_dependency_integrity.py"}:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            ctx.begin_unit(rel)
            for node in ast.walk(tree):
                words = _argv_literal(node)
                if not words:
                    continue
                where = f"{rel}:{getattr(node, 'lineno', '?')}"
                for i in range(len(words) - 1):
                    if _is_pip(words[i]) and words[i + 1] in {"install", "download", "wheel"}:
                        analyze_words(where, words[i:], ctx)
                        break
                else:
                    if _base(words[0]) == "uv":
                        analyze_words(where, words, ctx)


# ---------------------------------------------------------------- locks and metadata


def _components(root: Path) -> dict[str, tuple[str, str]]:
    sys.path.insert(0, str(root / "src"))
    try:
        from cognous_ocs.lock import COMPONENTS
    finally:
        sys.path.pop(0)
    return {c.distribution: (c.repository, c.sha) for c in COMPONENTS.values()}


def check_locks(root: Path, ctx: Context) -> None:
    req = root / "requirements"
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras: dict[str, list[str]] = pyproject["project"].get("optional-dependencies", {})
    want = _components(root)
    allowed_repos = {repo for repo, _ in want.values()}

    hashed: dict[str, _lockfile.HashedLock] = {}
    for path in sorted(req.glob("*.txt")):
        label = f"requirements/{path.name}"
        text = path.read_text(encoding="utf-8")
        if path.stem.endswith("-vcs"):
            check_vcs_file(label, text, want, allowed_repos, ctx)
        else:
            parsed = _lockfile.parse_hashed_lock(label, text)
            hashed[path.stem] = parsed
            for e in parsed.errors:
                ctx.error(e)
    for path in sorted(req.glob("*.in")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            body = line.split("#", 1)[0].strip()
            if body and (body.startswith("-") or "://" in body or "@" in body or "/" in body):
                ctx.error(
                    f"requirements/{path.name} line {number}: only plain requirement names are allowed"
                )

    for extra, requirements in extras.items():
        if extra in hashed:
            for e in _lockfile.check_ranges(
                f"requirements/{extra}.txt", hashed[extra], requirements
            ):
                ctx.error(e)
        for requirement in requirements:
            if " @ " in requirement:
                check_git_refs(f"pyproject.toml extra {extra}", requirement, allowed_repos, ctx)
        if any(" @ " in r for r in requirements):
            vcs = req / f"{extra}-vcs.txt"
            if not vcs.is_file():
                ctx.error(f"requirements/{extra}-vcs.txt is missing; extra {extra!r} has VCS pins")
            else:
                pins = {
                    r.split(" @ ")[0].strip(): r.split(" @ ", 1)[1].strip()
                    for r in requirements
                    if " @ " in r
                }
                filed = {
                    m[1]: m[2]
                    for m in re.finditer(
                        r"^([A-Za-z0-9._-]+) @ (git\+\S+)$", vcs.read_text(encoding="utf-8"), re.M
                    )
                }
                if pins != filed:
                    ctx.error(
                        f"requirements/{extra}-vcs.txt differs from the pyproject {extra!r} extra"
                    )

    # build toolchain
    build_system = pyproject.get("build-system", {})
    build = hashed.get("build")
    dev = hashed.get("dev")
    if build is None:
        ctx.error("requirements/build.txt (the locked build toolchain) is missing")
        return
    backend_reqs = list(build_system.get("requires", []))
    for e in _lockfile.check_ranges("requirements/build.txt", build, backend_reqs):
        ctx.error(e)
    for name in ("hatchling", "editables", "setuptools", "wheel"):
        if name not in build.versions():
            ctx.error(f"requirements/build.txt does not pin {name}")
    if dev is None:
        ctx.error("requirements/dev.txt is missing")
    else:
        for name, version in build.versions().items():
            if dev.versions().get(name) != version:
                ctx.error(
                    f"requirements/dev.txt and build.txt disagree on {name} ({dev.versions().get(name)} vs {version})"
                )


def check_vcs_file(
    label: str, text: str, want: dict[str, tuple[str, str]], allowed_repos: set[str], ctx: Context
) -> None:
    seen: dict[str, tuple[str, str]] = {}
    for number, line, open_end in _lockfile.logical_lines(text):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = None if open_end else re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*) @ (git\+\S+)", line)
        if m is None:
            ctx.error(
                f"{label} line {number}: not a strict `dist @ git+https://github.com/<repo>@<40 hex>` pin"
            )
            continue
        url = STRICT_GIT.fullmatch(m[2])
        if url is None:
            ctx.error(
                f"{label} line {number}: VCS reference {m[2]!r} is not git+https://github.com/<repo>@<full 40-hex SHA>"
            )
            continue
        if url[1] not in allowed_repos:
            ctx.error(f"{label} line {number}: repository {url[1]} is not in the allow-list")
            continue
        if m[1] in seen:
            ctx.error(f"{label} line {number}: {m[1]} is pinned twice")
        seen[m[1]] = (url[1], url[2])
    if seen != want:
        for dist in sorted(set(want) | set(seen)):
            if seen.get(dist) != want.get(dist):
                ctx.error(f"{label}: pin for {dist} differs from cognous_ocs.lock")


def check_git_refs(label: str, text: str, allowed_repos: set[str], ctx: Context) -> None:
    for m in GIT_URL.finditer(text):
        url = STRICT_GIT.fullmatch(m[0])
        if url is None:
            ctx.error(
                f"{label}: VCS reference {m[0]!r} is not git+https://github.com/<repo>@<full 40-hex SHA>"
            )
        elif url[1] not in allowed_repos:
            ctx.error(f"{label}: repository {url[1]} is not in the allow-list")


def check_repo_wide(root: Path, ctx: Context) -> None:
    allowed = {repo for repo, _ in _components(root).values()}
    for path in _repo.tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if path.name in INDEX_FILES:
            ctx.error(f"{rel}: alternate package index/resolver configuration is not allowed")
        scanned = (
            rel in {"pyproject.toml", "Makefile"}
            or rel.startswith(("requirements/", ".github/"))
            or path.name.startswith(("Dockerfile", "Containerfile"))
        )
        if not scanned:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            check_git_refs(f"{rel}:{number}", line, allowed, ctx)
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for key in ("uv", "pip", "poetry", "pdm"):
        if key in pyproject.get("tool", {}):
            ctx.error(f"pyproject.toml: [tool.{key}] can redirect package resolution; not allowed")


def check(root: Path) -> list[str]:
    errors: list[str] = []
    ctx = Context(root, errors)
    check_locks(root, ctx)
    check_github_files(root, ctx)
    check_makefile(root, ctx)
    check_python_argv(root, ctx)
    check_repo_wide(root, ctx)
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=_repo.ROOT)
    errors = check(ap.parse_args(argv).root)
    print("\n".join(errors) if errors else "dependency integrity: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
