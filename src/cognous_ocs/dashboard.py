"""`cognous-ocs dashboard`: one static, read-only HTML reading of verification and evidence artifacts.

Inputs (any subset, files or demo output directories): verification reports and mutation
reports from `cognous-verify` (ODES verification-report contract), evidence packs, the bounded
run records `acp run` writes, and the demo result. The page is a single file with inline CSS,
inline SVG and one inline script (copy-full-digest on click, pinned by a CSP hash). It makes no
request, reads nothing but the named inputs and adds no verdict, score, grade or ranking.

Every figure is a count or a k/n pair; a zero denominator prints n/a; a bare percentage is
never drawn. Colour is never the only carrier: each state also has a fill pattern and a word.
Inputs are untrusted: shapes and arithmetic are checked, a malformed input is refused (exit 2)
and nothing partial is rendered; every string is control-stripped and HTML-escaped.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cognous_ocs import __version__, render
from cognous_ocs.envelope import OCS_INPUT_INVALID, OCS_KIND_UNREGISTERED, OCS_PATHS, strip_controls

MAX_FILE_BYTES = 40_000_000
MAX_STRING = 4000
MAX_ROWS = 5000
MAX_EVENTS = 400
MAX_INPUTS = 50

DOES_NOT_PROVE = (
    "that any input is true: the page shows what each file says, and recomputes only the "
    "arithmetic inside it (tallies, denominators, sums)",
    "that any input conforms to its contract: this command does not run the contract validators",
    "that any digest matches a real file: digests are shown as recorded, not recomputed",
    "anything about records no input carries: a missing panel means no input supplied it",
    "that a verifier is correct, independent or exhaustive: mutation figures describe a "
    "synthetic corpus at a pinned revision and say nothing about other corpora or revisions",
    "that the demo's synthetic grant, identity or policy reflect any institution",
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]{7,64}$")
_EXT = re.compile(r"^x-[a-z0-9][a-z0-9._-]*$")

_VERDICTS = ("confirmed", "could_not_check", "refuted")
_MUT_VERDICTS = ("reject", "accept", "abstain", "error")
_EXPECTED = ("reject", "accept", "not_applicable")


class DashboardRefused(Exception):
    """An input cannot be rendered; carries the reason code to report."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _refuse(message: str, code: str = OCS_INPUT_INVALID) -> DashboardRefused:
    return DashboardRefused(code, message)


@dataclass
class Source:
    """One file that was read: its label, kind, parsed body and content digest."""

    label: str
    kind: str  # verification-report | evidence-pack | run-record | demo-result
    data: dict[str, Any]
    sha256: str
    size: int


@dataclass
class Bundle:
    sources: list[Source] = field(default_factory=list)


# ---------------------------------------------------------------- loading


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate member")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError("non-finite number")


def _parse(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _refuse(f"{label} is not strict JSON ({type(exc).__name__})") from exc


def _safe_label(text: str) -> str:
    return strip_controls(text)[:120]


def _read_file(path: Path, label: str) -> tuple[bytes, Any]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise _refuse(f"{label} is larger than the {MAX_FILE_BYTES // 1_000_000} MB limit")
    raw = path.read_bytes()
    return raw, _parse(raw, label)


def classify_kind(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    if doc.get("contract") == "verification-report":
        return "verification-report"
    if "pack_version" in doc:
        return "evidence-pack"
    if doc.get("command") == "demo" and "envelope_version" in doc:
        return "demo-result"
    if all(isinstance(doc.get(k), list) for k in ("decisions", "attempts", "observations")):
        return "run-record"
    return None


def load_source(path: Path, label: str) -> Source:
    """Read one file, decide its kind, validate it. Raises DashboardRefused."""
    label = _safe_label(label)
    raw, doc = _read_file(path, label)
    kind = classify_kind(doc)
    if kind is None:
        raise _refuse(f"{label} is not a recognised dashboard input")
    if not isinstance(doc, dict):
        raise _refuse(f"{label} is not a JSON object")
    if kind == "verification-report":
        check_report(doc, label)
    elif kind == "evidence-pack":
        try:
            render.load_pack(path)
        except render.RenderRefused as exc:
            raise DashboardRefused(exc.code, f"{label}: {exc.message}") from exc
    elif kind == "run-record":
        check_run_record(doc, label)
    else:
        check_demo_result(doc, label)
    return Source(label, kind, doc, hashlib.sha256(raw).hexdigest(), len(raw))


def collect(paths: list[Path], labels: list[str]) -> Bundle:
    """Resolve each input (file, or a demo output directory) into sources, in the order given."""
    bundle = Bundle()
    for path, label in zip(paths, labels, strict=True):
        if path.is_dir():
            found = _demo_dir_files(path)
            if not found:
                raise _refuse(f"{_safe_label(label)} is a directory with no demo output in it")
            for sub, sub_label in found:
                bundle.sources.append(load_source(sub, f"{_safe_label(label)}/{sub_label}"))
        else:
            bundle.sources.append(load_source(path, label))
        if len(bundle.sources) > MAX_INPUTS:
            raise _refuse(f"more than {MAX_INPUTS} inputs")
    return bundle


def _demo_dir_files(root: Path) -> list[tuple[Path, str]]:
    names: list[tuple[Path, str]] = []
    candidates = [(root / "demo-result.json", "demo-result.json")]
    control = root / "control"
    if control.is_dir() and not control.is_symlink():
        for op in sorted(p for p in control.iterdir() if p.is_dir() and not p.is_symlink()):
            candidates.append(
                (op / "out" / "bounded-run-record.json", f"control/{op.name}/bounded-run-record")
            )
    candidates.append((root / "outputs" / "evidence-pack.json", "outputs/evidence-pack.json"))
    for path, label in candidates:
        if path.is_symlink():
            raise _refuse(f"{_safe_label(label)} is a symbolic link", OCS_PATHS)
        if path.is_file():
            names.append((path, label))
    return names


# ---------------------------------------------------------------- validation helpers


def _is_int(v: object) -> bool:
    return type(v) is int and v >= 0


def _obj(v: Any, where: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise _refuse(f"{where} is not an object")
    extra = [k for k in v if k not in allowed and k != "extensions"]
    if extra:
        raise _refuse(f"{where} has an undeclared member")
    for k in v.get("extensions", {}) if isinstance(v.get("extensions"), dict) else []:
        if not _EXT.match(str(k)):
            raise _refuse(f"{where} has an extension name without the x- prefix")
    if "extensions" in v and not isinstance(v["extensions"], dict):
        raise _refuse(f"{where}.extensions is not an object")
    return v


def _list(v: Any, where: str, limit: int = MAX_ROWS) -> list[Any]:
    if not isinstance(v, list):
        raise _refuse(f"{where} is not a list")
    if len(v) > limit:
        raise _refuse(f"{where} has more than {limit} entries")
    return v


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v:
        raise _refuse(f"{where} is not a non-empty string")
    if len(v) > MAX_STRING:
        raise _refuse(f"{where} is longer than {MAX_STRING} characters")
    return v


def _digest(v: Any, where: str) -> str:
    if not isinstance(v, str) or not _DIGEST.match(v):
        raise _refuse(f"{where} is not a sha256 digest")
    return v


def _int(v: Any, where: str) -> int:
    if not _is_int(v):
        raise _refuse(f"{where} is not a non-negative integer")
    return int(v)


def _frac(v: Any, where: str) -> tuple[int, int]:
    o = _obj(v, where, {"k", "n"})
    k, n = _int(o.get("k"), f"{where}.k"), _int(o.get("n"), f"{where}.n")
    if k > n:
        raise _refuse(f"{where}: k exceeds n")
    return k, n


# ---------------------------------------------------------------- verification report


def check_report(doc: dict[str, Any], where: str) -> None:
    top = {
        "contract",
        "contract_version",
        "profile",
        "report_kind",
        "report_id",
        "generated_at",
        "verifier",
        "subject",
        "scope",
        "checks",
        "summary",
        "does_not_prove",
        "mutation_report",
    }
    _obj(doc, where, top)
    if doc.get("contract_version") != "0.1.0-draft":
        raise DashboardRefused(
            OCS_KIND_UNREGISTERED, f"{where}: verification-report version is not 0.1.0-draft"
        )
    if doc.get("report_kind") != "verification_report" or doc.get("profile") != (
        "verification-report/0.1"
    ):
        raise _refuse(f"{where}: report_kind or profile is not the draft's")
    _str(doc.get("report_id"), f"{where}.report_id")
    _str(doc.get("generated_at"), f"{where}.generated_at")
    v = _obj(
        doc.get("verifier"),
        f"{where}.verifier",
        {"name", "version", "command", "source_revision", "canonicalization_profile"},
    )
    for key in ("name", "version", "command", "source_revision"):
        _str(v.get(key), f"{where}.verifier.{key}")
    subject = _obj(
        doc.get("subject"),
        f"{where}.subject",
        {"canonicalization_profile", "inputs", "inputs_digest", "pinned"},
    )
    for i, inp in enumerate(_list(subject.get("inputs"), f"{where}.subject.inputs")):
        o = _obj(inp, f"{where}.subject.inputs[{i}]", {"role", "name", "digest", "size_bytes"})
        _str(o.get("role"), "input role")
        _str(o.get("name"), "input name")
        _digest(o.get("digest"), "input digest")
        _int(o.get("size_bytes"), "input size")
    _digest(subject.get("inputs_digest"), f"{where}.subject.inputs_digest")
    pinned = _obj(
        subject.get("pinned"),
        f"{where}.subject.pinned",
        {"controller_revision", "contract_versions", "reason_registry_digest"},
    )
    _str(pinned.get("controller_revision"), "controller_revision")
    _digest(pinned.get("reason_registry_digest"), "reason_registry_digest")
    cv = pinned.get("contract_versions")
    if not isinstance(cv, dict) or not cv:
        raise _refuse(f"{where}: pinned contract_versions is empty")
    for name, ver in cv.items():
        _str(name, "contract name")
        _str(ver, "contract version")
    scope = _obj(doc.get("scope"), f"{where}.scope", {"semantic_quality", "tiers_run"})
    if scope.get("semantic_quality") != "not_assessed":
        raise _refuse(f"{where}: scope.semantic_quality is not not_assessed")
    for t in _list(scope.get("tiers_run"), "tiers_run"):
        _str(t, "tier")
    counts = dict.fromkeys(_VERDICTS, 0)
    checks = _list(doc.get("checks"), f"{where}.checks")
    for i, c in enumerate(checks):
        counts[_check(c, f"{where}.checks[{i}]")] += 1
    s = _obj(
        doc.get("summary"),
        f"{where}.summary",
        {"checks", "confirmed", "could_not_check", "refuted"},
    )
    if s.get("checks") != len(checks) or any(s.get(k) != counts[k] for k in _VERDICTS):
        raise _refuse(f"{where}: summary does not match the checks as written")
    dnp = _list(doc.get("does_not_prove"), f"{where}.does_not_prove")
    if not dnp:
        raise _refuse(f"{where}: does_not_prove is empty")
    for i, item in enumerate(dnp):
        o = _obj(item, f"{where}.does_not_prove[{i}]", {"id", "statement"})
        _str(o.get("id"), "non-claim id")
        _str(o.get("statement"), "non-claim statement")
    mr = doc.get("mutation_report")
    if mr is None:
        if not checks:
            raise _refuse(f"{where}: no checks and no mutation report states nothing")
    else:
        _mutation(mr, f"{where}.mutation_report")


def _check(c: Any, where: str) -> str:
    o = _obj(
        c, where, {"check_id", "tier", "verdict", "tally", "could_not_check_reason", "findings"}
    )
    _str(o.get("check_id"), "check_id")
    _str(o.get("tier"), "tier")
    verdict = o.get("verdict")
    if verdict not in _VERDICTS:
        raise _refuse(f"{where}: verdict is not one of the three values")
    t = _obj(o.get("tally"), f"{where}.tally", {"confirmed", "could_not_check", "refuted", "n"})
    vals = {k: _int(t.get(k), f"{where}.tally.{k}") for k in (*_VERDICTS, "n")}
    if vals["n"] < 1 or vals["confirmed"] + vals["could_not_check"] + vals["refuted"] != vals["n"]:
        raise _refuse(f"{where}: tally does not sum to n")
    derived = (
        "refuted"
        if vals["refuted"]
        else ("could_not_check" if vals["could_not_check"] else "confirmed")
    )
    if derived != verdict:
        raise _refuse(f"{where}: verdict is not the one its tally derives")
    reason = o.get("could_not_check_reason")
    if reason is not None:
        _str(reason, "could_not_check_reason")
    findings = _list(o.get("findings"), f"{where}.findings")
    if bool(findings) != (verdict == "refuted"):
        raise _refuse(f"{where}: findings and a refuted verdict do not go together")
    for f in findings:
        fo = _obj(f, f"{where}.findings", {"code", "locator", "detail"})
        for key in ("code", "locator", "detail"):
            if not isinstance(fo.get(key), str) or len(fo[key]) > MAX_STRING:
                raise _refuse(f"{where}: a finding member is not a short string")
    return str(verdict)


def _mutation(m: Any, where: str) -> None:
    o = _obj(
        m,
        where,
        {
            "report_only",
            "operator_set_digest",
            "seed",
            "frozen_revision",
            "family_t",
            "family_s",
            "clean",
        },
    )
    if o.get("report_only") is not True:
        raise _refuse(f"{where}: report_only is not true")
    _digest(o.get("operator_set_digest"), "operator_set_digest")
    if type(o.get("seed")) is not int:
        raise _refuse(f"{where}: seed is not an integer")
    _str(o.get("frozen_revision"), "frozen_revision")
    ids: set[str] = set()
    for fam in ("family_t", "family_s", "clean"):
        for i, corpus in enumerate(_list(o.get(fam), f"{where}.{fam}")):
            cid = _corpus(fam, corpus, f"{where}.{fam}[{i}]")
            if cid in ids:
                raise _refuse(f"{where}: corpus id repeated across families")
            ids.add(cid)


def _corpus(fam: str, c: Any, where: str) -> str:
    if fam == "family_s":
        o = _obj(c, where, {"corpus_id", "context_only", "rows"})
        if o.get("context_only") is not True:
            raise _refuse(f"{where}: family S is not marked context_only")
    else:
        keys = {"corpus_id", "provenance", "corpus_digest"} | (
            {"categories", "scenarios"} if fam == "family_t" else {"rows"}
        )
        o = _obj(c, where, keys)
        if o.get("provenance") not in ("seed", "generated", "seed_valid_fixtures"):
            raise _refuse(f"{where}: provenance is not a known value")
        _digest(o.get("corpus_digest"), "corpus_digest")
    cid = _str(o.get("corpus_id"), "corpus_id")
    seen: set[str] = set()
    for i, row in enumerate(_list(o.get("rows" if fam != "family_t" else "categories"), where)):
        rw = f"{where}.rows[{i}]"
        if fam == "family_s":
            r = _obj(row, rw, {"source_id", "n_cases", "detected", "survived", "errored"})
            key = _str(r.get("source_id"), "source_id")
            parts = [_int(r.get(k), f"{rw}.{k}") for k in ("detected", "survived", "errored")]
            if sum(parts) != _int(r.get("n_cases"), f"{rw}.n_cases"):
                raise _refuse(f"{rw}: detected, survived and errored do not sum to n_cases")
        elif fam == "clean":
            r = _obj(row, rw, {"category", "false_rejection"})
            key = _str(r.get("category"), "category")
            _frac(r.get("false_rejection"), f"{rw}.false_rejection")
        else:
            key = _t_row(row, rw, str(o.get("provenance")))
        if key in seen:
            raise _refuse(f"{where}: a category or source appears twice in one corpus")
        seen.add(key)
    return cid


def _t_row(row: Any, where: str, provenance: str) -> str:
    r = _obj(
        row,
        where,
        {
            "category",
            "expected_verdict",
            "n_gen",
            "n_noop",
            "n_na",
            "n_eff",
            "verdicts",
            "detection",
            "regression",
            "false_acceptance",
            "abstained",
            "errored",
            "metric_impact",
        },
    )
    cat = _str(r.get("category"), "category")
    if r.get("expected_verdict") not in _EXPECTED:
        raise _refuse(f"{where}: expected_verdict is not a known value")
    n_gen, n_noop, n_na, n_eff = (
        _int(r.get(k), f"{where}.{k}") for k in ("n_gen", "n_noop", "n_na", "n_eff")
    )
    if n_eff != n_gen - n_noop - n_na:
        raise _refuse(f"{where}: n_eff is not n_gen - n_noop - n_na")
    vs = _obj(r.get("verdicts"), f"{where}.verdicts", set(_MUT_VERDICTS))
    if sum(_int(vs.get(k), f"{where}.verdicts.{k}") for k in _MUT_VERDICTS) != n_eff:
        raise _refuse(f"{where}: verdicts do not sum to n_eff")
    for key in ("false_acceptance", "abstained", "errored"):
        if r.get(key) is not None:
            k, n = _frac(r[key], f"{where}.{key}")
            if n != n_eff:
                raise _refuse(f"{where}: {key} denominator is not n_eff")
    if r.get("detection") is not None:
        d = _obj(r["detection"], f"{where}.detection", {"any_code", "intended_code"})
        a, an = _frac(d.get("any_code"), f"{where}.detection.any_code")
        i, inn = _frac(d.get("intended_code"), f"{where}.detection.intended_code")
        if an != n_eff or inn != n_eff or a != vs["reject"] or i > a:
            raise _refuse(f"{where}: detection does not match the verdict counts")
    if r.get("regression") is not None:
        g = _obj(r["regression"], f"{where}.regression", {"refused_with_intended_code"})
        k, n = _frac(g.get("refused_with_intended_code"), f"{where}.regression")
        if n != n_eff or k > vs["reject"]:
            raise _refuse(f"{where}: regression does not match the verdict counts")
    if provenance != "generated" and r.get("detection") is not None:
        raise _refuse(f"{where}: a seed corpus carries a detection figure")
    return cat


# ---------------------------------------------------------------- other inputs


def _when(value: Any, where: str) -> datetime:
    text = _str(value, where)
    try:
        t = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _refuse(f"{where} is not an ISO-8601 time") from exc
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def check_run_record(doc: dict[str, Any], where: str) -> None:
    _str(doc.get("run_id"), f"{where}.run_id")
    fields = (
        ("decisions", "decided_at", "result"),
        ("attempts", "started_at", "status"),
        ("observations", "observed_at", "state"),
    )
    total = 0
    for name, tkey, vkey in fields:
        items = _list(doc.get(name), f"{where}.{name}", MAX_EVENTS)
        total += len(items)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise _refuse(f"{where}.{name}[{i}] is not an object")
            _when(item.get(tkey), f"{where}.{name}[{i}].{tkey}")
            _str(item.get(vkey), f"{where}.{name}[{i}].{vkey}")
            reasons = item.get("reasons", [])
            if name == "decisions" and (
                not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons)
            ):
                raise _refuse(f"{where}.{name}[{i}].reasons is not a list of strings")
    if total > MAX_EVENTS:
        raise _refuse(f"{where} has more than {MAX_EVENTS} events")


def check_demo_result(doc: dict[str, Any], where: str) -> None:
    ext = doc.get("extensions")
    if not isinstance(ext, dict):
        raise _refuse(f"{where}: demo result carries no extensions")
    facts, steps = ext.get("x-facts"), ext.get("x-steps")
    if not isinstance(facts, dict) or not all(isinstance(v, bool) for v in facts.values()):
        raise _refuse(f"{where}: x-facts is not a map of booleans")
    if not isinstance(steps, list) or not all(
        isinstance(s, dict) and isinstance(s.get("ok"), bool) for s in steps
    ):
        raise _refuse(f"{where}: x-steps is not a list of step objects")
    if len(facts) > MAX_ROWS or len(steps) > MAX_ROWS:
        raise _refuse(f"{where}: too many facts or steps")
    if not isinstance(doc.get("outcome"), str) or not _is_int(doc.get("exit_code")):
        raise _refuse(f"{where}: outcome or exit_code is missing")


# ---------------------------------------------------------------- rendering


def _e(value: object) -> str:
    return html.escape(strip_controls("" if value is None else value), quote=True)


def frac(k: int, n: int) -> str:
    """k/n, or n/a when the denominator is zero. Never a percentage."""
    return "n/a" if n == 0 else f"{k}/{n}"


STATES = {
    "found": ("found a problem", "refuted, rejected"),
    "clear": ("found nothing wrong", "confirmed, accepted"),
    "unsure": ("could not check", "could not check, abstained"),
    "crash": ("verifier crashed", "error"),
}
_CHECK_STATE = {"confirmed": "clear", "could_not_check": "unsure", "refuted": "found"}
_MUT_STATE = {"reject": "found", "accept": "clear", "abstain": "unsure", "error": "crash"}


def _seg(parts: list[tuple[str, int]], total: int, label: str) -> str:
    """A segmented bar over `total`; zero parts are omitted; an empty total is a labelled n/a."""
    if total == 0:
        return f'<div class="seg empty" role="img" aria-label="{_e(label)}: n/a"></div>'
    cells = "".join(
        f'<i class="s-{state}" style="flex:{n} 1 0"></i>' for state, n in parts if n > 0
    )
    return f'<div class="seg" role="img" aria-label="{_e(label)}">{cells}</div>'


def _dg(value: str, label: str) -> str:
    """A recorded identifier: shortened with copy-full on click when scripts run; full otherwise."""
    text = strip_controls(value)
    body = text.split(":", 1)[-1]
    if not (_DIGEST.match(text) or _HEX.match(text)) or len(body) < 16:
        return f"<code>{_e(text)}</code>"
    prefix = text[: len(text) - len(body)]
    short = f"{prefix}{body[:8]}…{body[-4:]}"
    return (
        '<span class="dg">'
        f'<button type="button" class="short" data-full="{_e(text)}" data-label="{_e(label)}" '
        f'aria-label="Copy full {_e(label)} {_e(text)}" title="Click to copy the full value">'
        f"{_e(short)}</button>"
        f'<code class="full">{_e(text)}</code></span>'
    )


def _kv(rows: list[tuple[str, str]]) -> str:
    return "<dl>" + "".join(f"<dt>{_e(k)}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"


def _legend() -> str:
    items = "".join(
        f'<li><i class="sw s-{s}"></i><span><b>{_e(name)}</b> <small>({_e(words)})</small></span></li>'
        for s, (name, words) in STATES.items()
    )
    return f'<div class="key"><h2>Key to the bar fills</h2><ul class="legend">{items}</ul></div>'


def _nonempty(items: list[str]) -> str:
    return "".join(items)


def _checks_section(doc: dict[str, Any], sid: str) -> str:
    checks = doc["checks"]
    if not checks:
        return '<p class="note">This report carries no per-check results.</p>'
    s = doc["summary"]
    head = (
        f'<p class="fig-line"><b>{s["checks"]}</b> checks: {s["confirmed"]} confirmed, '
        f"{s['could_not_check']} could not check, {s['refuted']} refuted "
        "<small>(counts of checks)</small></p>"
        + _seg([(_CHECK_STATE[v], s[v]) for v in _VERDICTS], s["checks"], "Checks by result")
    )
    rows = []
    for c in checks:
        t = c["tally"]
        bar = _seg([(_CHECK_STATE[v], t[v]) for v in _VERDICTS], t["n"], f"{c['check_id']} items")
        reason = (
            f'<span class="muted">reason: {_e(c["could_not_check_reason"])}</span>'
            if c["could_not_check_reason"]
            else ""
        )
        findings = ""
        if c["findings"]:
            li = "".join(
                f"<li><code>{_e(f['code'])}</code> at <code>{_e(f['locator'])}</code>: {_e(f['detail'])}</li>"
                for f in c["findings"]
            )
            findings = f"<details><summary>{len(c['findings'])} finding(s)</summary><ul>{li}</ul></details>"
        rows.append(
            f'<li class="check"><div class="check-id"><code>{_e(c["check_id"])}</code>'
            f'<span class="tag">{_e(c["tier"])}</span></div>{bar}'
            f'<p class="tally"><span><i class="sw s-clear"></i>{t["confirmed"]} confirmed</span>'
            f'<span><i class="sw s-unsure"></i>{t["could_not_check"]} could not check</span>'
            f'<span><i class="sw s-found"></i>{t["refuted"]} refuted</span>'
            f'<span class="muted">of {t["n"]} items</span>{reason}</p>{findings}</li>'
        )
    return f'{head}<ol class="checks" id="{sid}-list">{"".join(rows)}</ol>'


def _identity(doc: dict[str, Any]) -> str:
    v, subj = doc["verifier"], doc["subject"]
    pinned = subj["pinned"]
    versions = ", ".join(f"{_e(k)} {_e(x)}" for k, x in sorted(pinned["contract_versions"].items()))
    rows = [
        ("Report", f"<code>{_e(doc['report_id'])}</code>"),
        ("Generated at", _e(doc["generated_at"])),
        ("Verifier", f"{_e(v['name'])} {_e(v['version'])} (<code>{_e(v['command'])}</code>)"),
        ("Verifier revision", _dg(v["source_revision"], "verifier revision")),
        ("Controller revision", _dg(pinned["controller_revision"], "controller revision")),
        ("Pinned contracts", versions),
        ("Reason registry", _dg(pinned["reason_registry_digest"], "reason registry digest")),
        ("Inputs digest", _dg(subj["inputs_digest"], "inputs digest")),
        ("Tiers run", _e(", ".join(doc["scope"]["tiers_run"]) or "none")),
        ("Semantic quality", "not assessed"),
    ]
    inputs = "".join(
        f"<tr><td>{_e(i['role'])}</td><td><code>{_e(i['name'])}</code></td>"
        f'<td>{_dg(i["digest"], "input digest")}</td><td class="num">{i["size_bytes"]}</td></tr>'
        for i in subj["inputs"]
    )
    table = (
        f'<details class="tview"><summary>Inputs the verifier read ({len(subj["inputs"])})</summary>'
        '<div class="scroll"><table><thead><tr>'
        "<th>Role</th><th>Name</th><th>Digest</th><th>Bytes</th></tr></thead>"
        f"<tbody>{inputs}</tbody></table></div></details>"
        if inputs
        else ""
    )
    return f'<div class="identity">{_kv(rows)}</div>{table}'


def _expect_text(e: str) -> str:
    return {"reject": "expects reject", "accept": "expects accept"}.get(e, "no expectation")


def _tile_measure(row: dict[str, Any]) -> tuple[str, int, int]:
    """The one figure a tile leads with: what it counts, k, and its denominator."""
    vs, n = row["verdicts"], row["n_eff"]
    if row["regression"] is not None:
        k = row["regression"]["refused_with_intended_code"]["k"]
        return "refused with the intended code", k, n
    if row["expected_verdict"] == "accept":
        return "accepted", vs["accept"], n
    if row["expected_verdict"] == "reject":
        return "rejected", vs["reject"], n
    return "counted", 0, n


def _tiles(corpus: dict[str, Any]) -> str:
    items = []
    for r in corpus["categories"]:
        vs, n = r["verdicts"], r["n_eff"]
        label, k, d = _tile_measure(r)
        fig = frac(k, d)
        aria = (
            f"{r['category']}: "
            + ", ".join(f"{vs[x]} {x}" for x in _MUT_VERDICTS)
            + f" of {n} effective"
        )
        bar = _seg([(_MUT_STATE[x], vs[x]) for x in _MUT_VERDICTS], n, aria)
        text = "n/a" if d == 0 else f"{fig} {label}"
        other = d - k if d else 0
        note = f'<div class="tile-n">{other} other outcome(s)</div>' if other else ""
        items.append(
            f'<li class="tile{" differs" if other else ""}"><div class="tile-h"><b>{_e(r["category"])}</b>'
            f"<span>{_e(_expect_text(r['expected_verdict']))}</span></div>{bar}"
            f'<div class="tile-f"><span class="fig">{_e(text)}</span></div>{note}</li>'
        )
    return f'<ul class="tiles">{"".join(items)}</ul>'


def _t_table(corpus: dict[str, Any]) -> str:
    head = "".join(
        f"<th>{h}</th>"
        for h in (
            "Category",
            "Expects",
            "Generated",
            "No-op",
            "n/a",
            "Effective",
            "Reject",
            "Accept",
            "Abstain",
            "Error",
            "Intended code",
        )
    )
    rows = []
    for r in corpus["categories"]:
        vs, n = r["verdicts"], r["n_eff"]
        if r["detection"] is not None:
            ic = frac(r["detection"]["intended_code"]["k"], n)
        elif r["regression"] is not None:
            ic = frac(r["regression"]["refused_with_intended_code"]["k"], n)
        else:
            ic = "n/a"
        cells = [
            _e(r["category"]),
            _e(_expect_text(r["expected_verdict"])),
            str(r["n_gen"]),
            str(r["n_noop"]),
            str(r["n_na"]),
            str(n),
            *(frac(vs[x], n) for x in _MUT_VERDICTS),
            ic,
        ]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<details class="tview"><summary>Table view</summary><div class="scroll"><table>'
        f"<caption>{_e(corpus['corpus_id'])}: counts per category</caption>"
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></details>"
    )


def _mutation_section(doc: dict[str, Any], sid: str) -> str:
    m = doc["mutation_report"]
    out = [
        f'<h3 id="{sid}-mut">Mutation results</h3>',
        '<p class="note">Report only. Trace mutants (family T), clean traces and source mutants '
        "(family S) are three separate populations; each corpus is shown alone and nothing is "
        "pooled across them. Each figure is k out of the denominator printed beside it.</p>",
        _kv(
            [
                ("Operator set", _dg(m["operator_set_digest"], "operator set digest")),
                ("Seed", _e(m["seed"])),
                ("Frozen at", _dg(m["frozen_revision"], "frozen revision")),
            ]
        ),
    ]
    for c in m["family_t"]:
        basis = (
            "regression check on the seed fixtures"
            if c["provenance"] == "seed"
            else "held-out generated mutants"
        )
        out.append(
            f'<section class="panel corpus" aria-labelledby="{sid}-{_e(c["corpus_id"])}">'
            f'<h4 id="{sid}-{_e(c["corpus_id"])}">Family T: <code>{_e(c["corpus_id"])}</code> '
            f"<small>{_e(basis)}</small></h4>"
            f'<p class="muted">Corpus {_dg(c["corpus_digest"], "corpus digest")}. '
            "Each tile: the bar splits that category's effective mutants by what the verifier said; "
            "the figure is k/n over those effective mutants.</p>"
            f"{_tiles(c)}{_t_table(c)}</section>"
        )
    if m["clean"]:
        out.append(_clean_panel(m["clean"], sid))
    for s in m["family_s"]:
        out.append(_family_s(s, sid))
    return "".join(out)


def _clean_panel(corpora: list[dict[str, Any]], sid: str) -> str:
    parts = []
    for c in corpora:
        tiles = []
        for r in c["rows"]:
            k, n = r["false_rejection"]["k"], r["false_rejection"]["n"]
            label = f"{r['category']}: {k} rejected of {n} clean traces"
            bar = _seg([("found", k), ("none", n - k)], n, label)
            tiles.append(
                f'<li class="tile"><div class="tile-h"><b>{_e(r["category"])}</b></div>{bar}'
                f'<div class="tile-f"><span class="fig">{_e(frac(k, n))} rejected</span></div></li>'
            )
        rows = "".join(
            f"<tr><td>{_e(r['category'])}</td><td>{frac(r['false_rejection']['k'], r['false_rejection']['n'])}</td></tr>"
            for r in c["rows"]
        )
        parts.append(
            f"<h4>Clean corpus <code>{_e(c['corpus_id'])}</code> <small>{_e(c['provenance'].replace('_', ' '))}</small></h4>"
            f'<p class="muted">Corpus {_dg(c["corpus_digest"], "corpus digest")}. Unmutated traces; '
            "the filled part is traces the verifier rejected, the empty part is traces it did not "
            "reject (accepted or abstained, which this report does not split).</p>"
            f'<ul class="tiles dense">{"".join(tiles)}</ul>'
            f'<details class="tview"><summary>Table view</summary><div class="scroll"><table>'
            f"<caption>{_e(c['corpus_id'])}: clean traces rejected</caption><thead><tr><th>Category</th>"
            f"<th>Rejected / clean traces</th></tr></thead><tbody>{rows}</tbody></table></div></details>"
        )
    return (
        f'<section class="panel" aria-labelledby="{sid}-clean"><h3 id="{sid}-clean">'
        "Clean false rejection</h3>" + "".join(parts) + "</section>"
    )


def _family_s(s: dict[str, Any], sid: str) -> str:
    rows = []
    for r in s["rows"]:
        bar = _seg(
            [("found", r["detected"]), ("clear", r["survived"]), ("crash", r["errored"])],
            r["n_cases"],
            f"{r['source_id']} cases",
        )
        rows.append(
            f'<li class="check"><div class="check-id"><code>{_e(r["source_id"])}</code></div>{bar}'
            f'<p class="tally"><span>{frac(r["detected"], r["n_cases"])} detected</span>'
            f"<span>{frac(r['survived'], r['n_cases'])} survived</span>"
            f"<span>{frac(r['errored'], r['n_cases'])} errored</span></p></li>"
        )
    return (
        f'<section class="panel context"><h3>Family S: <code>{_e(s["corpus_id"])}</code> '
        '<small>context only</small></h3><p class="muted">Source mutants ask whether the tests '
        "notice a removed control. They say nothing about the verifier on damaged records and "
        f'share no denominator with family T.</p><ol class="checks">{"".join(rows)}</ol></section>'
    )


def _evidence_section(src: Source) -> str:
    pack = src.data
    status = render.classify(pack)
    blocked = pack.get("blocked_actions", [])
    counts = [
        ("Declared actions", len(pack.get("action_inventory", []))),
        ("Recorded as blocked", sum(1 for s in status.values() if s == "blocked")),
        (
            "Default posture allowed, no block recorded",
            sum(1 for s in status.values() if s == "allowed"),
        ),
        ("Reliance sources", len(pack.get("reliance_summary", []))),
        ("Replay bundles", len(pack.get("replay_bundles", []))),
    ]
    figs = "".join(f"<li><b>{n}</b><span>{_e(k)}</span></li>" for k, n in counts)
    rows = "".join(
        f"<tr><td><code>{_e(b.get('action_name'))}</code></td><td>{_e(b.get('tool_name'))}</td>"
        f'<td>{_e(b.get("reason_summary"))}</td><td class="num">{_e(b.get("count"))}</td></tr>'
        for b in blocked
    )
    table = (
        '<div class="scroll"><table><caption>Blocked actions the pack records</caption><thead><tr>'
        f"<th>Action</th><th>Tool</th><th>Reason</th><th>Count</th></tr></thead><tbody>{rows}</tbody></table></div>"
        if rows
        else '<p class="note">The pack records no blocked actions.</p>'
    )
    return (
        f'<ul class="counts" aria-label="Counts from the pack">{figs}</ul>{table}'
        '<p class="muted">Counts of what the pack lists. The pack was not validated here; '
        "<code>cognous-ocs evidence render</code> shows the full pack.</p>"
    )


def _demo_section(src: Source) -> str:
    d = src.data
    facts: dict[str, bool] = d["extensions"]["x-facts"]
    steps: list[dict[str, Any]] = d["extensions"]["x-steps"]
    held = sum(1 for v in facts.values() if v)
    ok = sum(1 for s in steps if s["ok"])
    li = "".join(
        f'<li><span class="tag">{"held" if v else "did not hold"}</span> {_e(k)}</li>'
        for k, v in facts.items()
    )
    return (
        f'<p class="fig-line">The demo\'s own result, relayed as recorded: outcome '
        f"<b>{_e(d['outcome'])}</b>, exit code {_e(d['exit_code'])}. This page does not assess it.</p>"
        f'<p class="tally"><span>{frac(held, len(facts))} facts held</span>'
        f"<span>{frac(ok, len(steps))} steps ended as expected</span></p>"
        + _seg([("clear", held), ("found", len(facts) - held)], len(facts), "Demo facts held")
        + f'<details class="tview"><summary>Facts</summary><ul class="facts">{li}</ul></details>'
    )


_KIND_ORDER = {"decision": 0, "attempt": 1, "observation": 2}


def _events(doc: dict[str, Any]) -> list[dict[str, Any]]:
    ev: list[tuple[datetime, int, int, dict[str, Any]]] = []
    spec = (
        ("decision", "decisions", "decided_at", "result"),
        ("attempt", "attempts", "started_at", "status"),
        ("observation", "observations", "observed_at", "state"),
    )
    for kind, name, tkey, vkey in spec:
        for i, item in enumerate(doc[name]):
            detail = ""
            if kind == "decision" and item.get("reasons"):
                detail = ", ".join(item["reasons"])
            ev.append(
                (
                    _when(item[tkey], "time"),
                    _KIND_ORDER[kind],
                    i,
                    {
                        "kind": kind,
                        "value": str(item[vkey]),
                        "time": str(item[tkey]),
                        "detail": detail,
                    },
                )
            )
    ev.sort(key=lambda t: t[:3])
    return [e[3] for e in ev]


def _timeline(src: Source) -> str:
    events = _events(src.data)
    if not events:
        return '<p class="note">The record has no decisions, attempts or observations.</p>'
    pad = 40
    step = min(190, max(118, 1000 // max(1, len(events) - 1)))
    width = pad * 2 + step * (len(events) - 1) + 40
    marks, labels = [], []
    for i, e in enumerate(events):
        x = pad + 20 + i * step
        y = 52
        if e["kind"] == "decision":
            shape = f'<path d="M{x},{y - 11}L{x + 11},{y}L{x},{y + 11}L{x - 11},{y}Z" class="m-decision"/>'
        elif e["kind"] == "attempt":
            shape = f'<circle cx="{x}" cy="{y}" r="9" class="m-attempt"/>'
        else:
            shape = f'<rect x="{x - 8}" y="{y - 8}" width="16" height="16" class="m-observation"/>'
        value = strip_controls(e["value"])
        short = value if len(value) <= 15 else value[:14] + "…"
        reasons = [r for r in strip_controls(e["detail"]).split(", ") if r]
        detail_short = ""
        if reasons:
            detail_short = reasons[0][:17] + ("\u2026" if len(reasons[0]) > 17 else "")
            detail_short += f" +{len(reasons) - 1}" if len(reasons) > 1 else ""
        date, _, clock = strip_controls(e["time"]).replace("Z", " UTC").partition("T")
        full = f"{e['kind']} {value} at {strip_controls(e['time'])}" + (
            f" ({strip_controls(e['detail'])})" if e["detail"] else ""
        )
        marks.append(f"<g><title>{_e(full)}</title>{shape}</g>")
        labels.append(
            f'<text x="{x}" y="86" text-anchor="middle" class="l-kind">{_e(e["kind"])}</text>'
            f'<text x="{x}" y="102" text-anchor="middle" class="l-val">{_e(short)}</text>'
            f'<text x="{x}" y="118" text-anchor="middle" class="l-detail">{_e(detail_short)}</text>'
            f'<text x="{x}" y="138" text-anchor="middle" class="l-time">{_e(clock[:8])}</text>'
            f'<text x="{x}" y="152" text-anchor="middle" class="l-time">{_e(date)}</text>'
        )
    svg = (
        f'<svg viewBox="0 0 {width} 166" width="{width}" height="166" role="img" '
        f'aria-label="Ordered strip of {len(events)} recorded events; table view follows">'
        f'<line x1="{pad}" y1="52" x2="{width - pad}" y2="52" class="axis"/>'
        f"{''.join(marks)}{''.join(labels)}</svg>"
    )
    rows = "".join(
        f'<tr><td class="num">{i + 1}</td><td>{_e(e["kind"])}</td><td>{_e(e["value"])}</td>'
        f"<td>{_e(e['time'])}</td><td>{_e(e['detail'])}</td></tr>"
        for i, e in enumerate(events)
    )
    return (
        '<div class="scroll strip" tabindex="0" role="region" aria-label="Event strip, scrolls sideways">'
        f"{svg}</div>"
        '<ul class="legend marks"><li><svg width="18" height="18" aria-hidden="true"><path d="M9,1L17,9L9,17L1,9Z" class="m-decision"/></svg>decision</li>'
        '<li><svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="7" class="m-attempt"/></svg>attempt</li>'
        '<li><svg width="18" height="18" aria-hidden="true"><rect x="2" y="2" width="14" height="14" class="m-observation"/></svg>observation</li></ul>'
        '<p class="muted">Events are ordered by recorded time, then decision, attempt, observation, '
        "then record order. Spacing is by order, not to a time scale; equal timestamps stay equal.</p>"
        '<details class="tview"><summary>Table view</summary><div class="scroll"><table>'
        "<caption>Recorded events in strip order</caption><thead><tr><th>#</th><th>Kind</th>"
        "<th>Result, status or state</th><th>Recorded at</th><th>Reasons</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></details>"
    )


_SCRIPT = """(function(){var d=document;d.documentElement.classList.add('js');
var live=d.getElementById('copy-status');
function done(b,ok){live.textContent=ok?'Copied '+b.getAttribute('data-label'):'Copy failed. Select the value instead.';}
function fallback(b,t){var a=d.createElement('textarea');a.value=t;a.setAttribute('readonly','');
a.style.position='fixed';a.style.opacity='0';d.body.appendChild(a);a.select();var ok=false;
try{ok=d.execCommand('copy');}catch(x){}d.body.removeChild(a);done(b,ok);}
d.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('button[data-full]');
if(!b)return;var t=b.getAttribute('data-full');
if(navigator.clipboard&&navigator.clipboard.writeText){
navigator.clipboard.writeText(t).then(function(){done(b,true);},function(){fallback(b,t);});}
else{fallback(b,t);}});})();"""

_CSS = """
:root{color-scheme:light;--page:#f3f4f6;--surface:#fcfcfb;--ink:#14181f;--ink2:#444b59;--muted:#5f6674;--rule:#d8dbe1;--track:#e3e5ea;
--clear:#2a78d6;--found:#eb6834;--unsure:#898781;--crash:#14181f;--slab:#14181f;--slab-ink:#f4f5f7;--slab-muted:#b9bfcb;--focus:#1b5fb4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0e10;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#a3a198;--rule:#33332f;--track:#2c2c2a;
--clear:#3987e5;--found:#d95926;--unsure:#898781;--crash:#f0efec;--slab:#23252b;--slab-ink:#f0efec;--slab-muted:#b2b0a6;--focus:#7db2f2}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:74rem;margin:0 auto;padding:0 16px 4rem}
h1,h2,h3,h4{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;font-weight:600;line-height:1.2;margin:0}
h1{font-size:clamp(1.9rem,4.5vw,2.9rem);max-width:22ch}
h2{font-size:1.55rem;margin:3rem 0 .4rem;padding-top:1rem;border-top:3px solid var(--ink)}
h3{font-size:1.2rem;margin:2rem 0 .5rem}h4{font-size:1.05rem;margin:1.5rem 0 .25rem}
h2 small,h3 small,h4 small{font:400 .8rem system-ui,sans-serif;color:var(--muted);margin-left:.4rem}
code{font:.88em ui-monospace,Menlo,Consolas,monospace;overflow-wrap:anywhere}
a{color:var(--focus)}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px;border-radius:2px}
.muted{color:var(--muted);font-size:.9rem;margin:.3rem 0}
.note{color:var(--ink2);margin:.4rem 0}
.slab{background:var(--slab);color:var(--slab-ink);padding:2.4rem 0 2.2rem}
.slab .inner{max-width:74rem;margin:0 auto;padding:0 16px;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(16rem,1fr);gap:1.5rem 3rem;align-items:end}
.key{background:var(--surface);color:var(--ink);border-radius:4px;padding:.9rem 1.1rem}
.key h2{font:600 .95rem system-ui,sans-serif;border:0;margin:0 0 .5rem;padding:0}
.key .legend{flex-direction:column;margin:0;gap:.45rem}
.slab p{margin:.8rem 0 0;max-width:62ch;color:var(--slab-muted);font-size:1.05rem}
.slab nav{margin-top:1.4rem;font-size:.92rem}
.slab nav a{color:var(--slab-ink);margin-right:1.1rem;white-space:nowrap}
.boundary{background:var(--surface);border:1px solid var(--rule);border-left:6px solid var(--ink);padding:1rem 1.2rem;margin:1.6rem 0 0}
.boundary h2{border:0;margin:0 0 .4rem;padding:0;font-size:1.25rem}
.boundary ul{margin:.3rem 0 0;padding-left:1.2rem;columns:2 26rem;column-gap:2.5rem}.boundary li{margin:.25rem 0;max-width:80ch}
.boundary h3{font-size:1rem;margin:1rem 0 .2rem}
.boundary li{break-inside:avoid}
.panel{background:var(--surface);border:1px solid var(--rule);padding:1rem 1.2rem 1.2rem;margin:1rem 0}
.panel.context{opacity:.96;border-style:dashed}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:.4rem 1.4rem;margin:1rem 0 0;padding:0;font-size:.9rem}
.legend li{display:flex;align-items:center;gap:.45rem}.legend small{color:var(--muted)}
.sw{display:inline-block;width:22px;height:12px;border-radius:2px 4px 4px 2px;vertical-align:-1px;margin-right:.3rem}
.legend .sw{margin:0}
.seg{display:flex;gap:2px;height:14px;background:var(--track);border-radius:0 4px 4px 0;overflow:hidden}
.seg i{display:block;min-width:3px}
.seg.empty{background:repeating-linear-gradient(135deg,var(--track) 0 6px,var(--surface) 6px 8px)}
.s-clear{background:var(--clear)}
.s-found{background:repeating-linear-gradient(45deg,var(--found) 0 4px,color-mix(in srgb,var(--found) 55%,#000) 4px 6px)}
.s-unsure{background:radial-gradient(circle at 3px 3px,color-mix(in srgb,var(--unsure) 45%,#000) 1.2px,transparent 1.6px) 0 0/6px 6px,var(--unsure)}
.s-crash{background:linear-gradient(45deg,transparent 45%,var(--surface) 45% 55%,transparent 55%),linear-gradient(135deg,transparent 45%,var(--surface) 45% 55%,transparent 55%),var(--crash);background-size:8px 8px}
.s-none{background:transparent}
.checks{list-style:none;margin:1rem 0 0;padding:0}
.check{display:grid;grid-template-columns:minmax(12rem,1fr) 3fr;gap:.2rem 1.2rem;align-items:center;padding:.7rem 0;border-top:1px solid var(--rule)}
.check .seg{grid-column:2;grid-row:1}.check-id{grid-column:1;grid-row:1}
.check .tally,.check details{grid-column:2}
.check-id{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.tag{font-size:.75rem;border:1px solid var(--muted);border-radius:3px;padding:0 .35rem;color:var(--ink2);white-space:nowrap}
.tally{display:flex;flex-wrap:wrap;gap:.2rem 1.2rem;margin:.1rem 0;font-size:.9rem;color:var(--ink2)}
.tally .muted{margin:0}
.fig-line{margin:.6rem 0 .4rem}
.tiles{list-style:none;margin:.8rem 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(10.2rem,1fr));gap:.7rem}
.tile{border:1px solid var(--rule);padding:.55rem .65rem .6rem;background:var(--surface)}
.tile-h{display:flex;justify-content:space-between;gap:.4rem;align-items:baseline;margin-bottom:.4rem}
.tile-h b{font-size:1.05rem}.tile-h span{font-size:.72rem;color:var(--muted)}
.tile-f{margin-top:.35rem;font-size:.85rem}.tile-n{font-size:.78rem;color:var(--ink2)}
.tile.differs{border:2px solid var(--ink);padding:calc(.55rem - 1px) calc(.65rem - 1px) calc(.6rem - 1px)}
.tiles.dense{grid-template-columns:repeat(auto-fill,minmax(6.6rem,1fr));gap:.45rem}
.dense .tile{padding:.4rem .5rem}.dense .tile-h{margin-bottom:.3rem}.dense .tile-f{font-size:.78rem;margin-top:.25rem}
.dense .seg{height:10px;box-shadow:inset 0 0 0 1px var(--rule)}
.panel>h3:first-child,.panel>h4:first-child{margin-top:.2rem}.fig{font-variant-numeric:tabular-nums;font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1.2rem;margin:.8rem 0}
dt{color:var(--muted);font-size:.9rem}dd{margin:0;overflow-wrap:anywhere}
@media (min-width:900px){.identity dl{grid-template-columns:max-content minmax(0,1fr) max-content minmax(0,1fr)}}
.dg .short{font:.88em ui-monospace,Menlo,Consolas,monospace;background:none;color:var(--ink);border:0;border-bottom:1px dashed var(--muted);padding:0;cursor:copy}
.dg .short:hover{background:var(--track)}
.js .dg .full{display:none}
html:not(.js) .dg .short{display:none}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.6rem 0}
caption{text-align:left;color:var(--muted);font-size:.85rem;padding-bottom:.3rem}
th,td{border-bottom:1px solid var(--rule);padding:.35rem .6rem;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;white-space:nowrap}td.num{font-variant-numeric:tabular-nums}
details.tview{margin-top:.8rem}summary{cursor:pointer;color:var(--ink2)}
details ul{margin:.4rem 0 .2rem 1.2rem;padding:0;font-size:.9rem}
.counts{list-style:none;display:flex;flex-wrap:wrap;gap:.7rem;padding:0;margin:.8rem 0}
.counts li{border:1px solid var(--rule);padding:.5rem .9rem;min-width:9rem}.counts b{display:block;font:600 1.6rem/1.1 "Iowan Old Style",Georgia,serif}
.counts span{font-size:.82rem;color:var(--ink2)}
.facts{list-style:none;margin-left:0}.facts li{margin:.2rem 0}
.strip{border:1px solid var(--rule);background:var(--surface);margin-top:.8rem}
.strip svg{display:block}
.axis{stroke:var(--muted);stroke-width:2}
.m-decision,.m-attempt,.m-observation{fill:var(--ink);stroke:var(--surface);stroke-width:2}
.m-attempt{fill:var(--surface);stroke:var(--ink)}.m-observation{fill:var(--ink2);stroke:var(--surface)}
.m-attempt{stroke-width:3}
.l-kind{font:600 11px system-ui,sans-serif;fill:var(--ink2)}.l-val{font:12px ui-monospace,Menlo,monospace;fill:var(--ink)}
.l-detail{font:11px ui-monospace,Menlo,monospace;fill:var(--ink2)}.l-time{font:10.5px system-ui,sans-serif;fill:var(--muted)}
.legend.marks svg{vertical-align:middle}
footer{margin-top:3rem;color:var(--muted);font-size:.85rem}
#copy-status{position:absolute;left:-9999px}
@media (max-width:720px){
.check{grid-template-columns:1fr}.check .seg,.check-id,.check .tally,.check details{grid-column:1;grid-row:auto}
dl{grid-template-columns:1fr;gap:0}dt{margin-top:.5rem}
.tiles{grid-template-columns:repeat(auto-fill,minmax(8.6rem,1fr))}
.slab nav a{display:inline-block;margin:.2rem 1rem .2rem 0}
.slab .inner{grid-template-columns:1fr}}
@media print{
:root{--page:#fff}body{background:#fff}.slab{background:#fff;color:#000;border-bottom:3px solid #000}.slab p,.slab nav a{color:#000}.slab .inner{display:block}
.js .dg .full{display:inline}.js .dg .short{display:none}
.panel,.tile,.check{break-inside:avoid}details>*{display:block}.scroll{overflow:visible}
a{color:#000}}
@media (forced-colors:active){.seg i,.sw{forced-color-adjust:none}}
"""


def _csp() -> str:
    h = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    return f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{h}'"


def render_html(bundle: Bundle) -> str:
    sections: list[str] = []
    nav: list[tuple[str, str]] = []
    dnp_seen: dict[str, str] = {}
    for n, src in enumerate(bundle.sources, 1):
        sid = f"in{n}"
        label = _e(src.label)
        ident = (
            f'<p class="muted">File <code>{label}</code>, {src.size} bytes, '
            f"sha256 {_dg('sha256:' + src.sha256, 'file digest')}</p>"
        )
        if src.kind == "verification-report":
            d = src.data
            title = (
                "Mutation report"
                if d["mutation_report"] and not d["checks"]
                else "Verification report"
            )
            nav.append((sid, f"{title} {n}"))
            for item in d["does_not_prove"]:
                dnp_seen.setdefault(item["id"], item["statement"])
            body = ident + _identity(d)
            if d["checks"]:
                body += f'<h3 id="{sid}-checks">Per-check results</h3>' + _checks_section(d, sid)
            if d["mutation_report"]:
                body += _mutation_section(d, sid)
        elif src.kind == "evidence-pack":
            title = "Evidence pack"
            nav.append((sid, f"{title} {n}"))
            body = ident + _evidence_section(src)
        elif src.kind == "run-record":
            title = "Run record"
            nav.append((sid, f"{title} {n}"))
            body = ident + _kv([("Run", f"<code>{_e(src.data['run_id'])}</code>")]) + _timeline(src)
        else:
            title = "Demo result"
            nav.append((sid, f"{title} {n}"))
            body = ident + _demo_section(src)
        sections.append(
            f'<section aria-labelledby="{sid}"><h2 id="{sid}">{_e(title)}: <code>{label}</code></h2>{body}</section>'
        )
    own = "".join(f"<li>{_e(t)}</li>" for t in DOES_NOT_PROVE)
    theirs = "".join(f"<li><code>{_e(k)}</code>: {_e(v)}</li>" for k, v in dnp_seen.items())
    theirs_block = (
        f'<h3>The reports\' own non-claims, quoted</h3><ul class="quoted">{theirs}</ul>'
        if theirs
        else ""
    )
    links = "".join(f'<a href="#{i}">{_e(t)}</a>' for i, t in nav)
    count = len(bundle.sources)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_csp()}">
<meta name="color-scheme" content="light dark">
<title>Report reading: {count} input(s)</title>
<style>{_CSS}</style>
<script>{_SCRIPT}</script>
</head>
<body>
<header class="slab"><div class="inner">
<div class="lede">
<h1>This page reads reports. It adds no verdict.</h1>
<p>A static view of {count} file(s) you named. Every figure is a count taken from them, printed as k out of its denominator. Nothing is summed into one number and nothing is ranked. The page is not an attestation and carries no approval of anything.</p>
<nav aria-label="Inputs on this page">{links}</nav>
</div>
{_legend()}
</div></header>
<main>
<section class="boundary" aria-labelledby="dnp" style="margin-top:1.6rem"><h2 id="dnp">What this page does not prove</h2>
<ul>{own}</ul>{theirs_block}</section>
{"".join(sections)}
<footer>Rendered by cognous-ocs {_e(__version__)} from the files as given, offline. No request is made and no timestamp of rendering is embedded, so the same inputs give the same bytes. Hover or focus a digest and click to copy the whole value.</footer>
<div id="copy-status" role="status" aria-live="polite"></div>
</main>
</body>
</html>
"""


def run_digest(page: bytes) -> str:
    return hashlib.sha256(page).hexdigest()


def input_files(path: Path) -> list[Path]:
    """Every file `collect` would read for this input (for the output-alias check)."""
    return [p for p, _ in _demo_dir_files(path)] if path.is_dir() else [path]


def run_dashboard(out_raw: str, inputs: list[str], *, strict: bool = False) -> Any:
    """Read the named inputs and write one HTML file. Returns a cli.Outcome."""
    from cognous_ocs import cli
    from cognous_ocs.envelope import INVALID, PASS, Result
    from cognous_ocs.paths import PathRefused, confine_output, refuse_alias

    subject: dict[str, Any] = {"kind": "dashboard", "inputs": len(inputs)}

    def refused(code: str, message: str, diag: Path | None = None) -> Any:
        return cli.Outcome(Result("dashboard", INVALID, [code], subject, messages=[message]), diag)

    if not inputs:
        return refused(
            OCS_INPUT_INVALID, "no input given; name at least one file or demo directory"
        )
    try:
        out = confine_output(out_raw)
        paths: list[Path] = []
        for raw in inputs:
            if "\r" in raw or "\n" in raw or "\x00" in raw:
                raise PathRefused("input path contains a CR, LF or NUL character")
            p = Path(raw)
            if not (p.is_file() or p.is_dir()):
                raise PathRefused("input path is not a readable file or directory")
            paths.append(p)
        refuse_alias([f for p in paths for f in input_files(p)], [out])
    except PathRefused as exc:
        return refused(OCS_PATHS, str(exc))
    except DashboardRefused as exc:
        return refused(exc.code, exc.message)
    diag = out.parent
    try:
        bundle = collect(paths, [p.name or str(p) for p in paths])
    except DashboardRefused as exc:
        return refused(exc.code, exc.message, diag)
    except OSError as exc:
        return refused(
            OCS_INPUT_INVALID, f"an input could not be read ({type(exc).__name__})", diag
        )
    page = render_html(bundle).encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(page)
    kinds: dict[str, int] = {}
    for s in bundle.sources:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    return cli.Outcome(
        Result(
            "dashboard",
            PASS,
            [],
            {**subject, "kinds": dict(sorted(kinds.items()))},
            does_not_prove=list(DOES_NOT_PROVE),
            messages=[
                f"wrote {len(bundle.sources)} input(s) as one page to {out.name} (sha256 {run_digest(page)[:16]})",
                "the page reads the inputs and adds no verdict; the inputs were not contract-validated by this command",
            ],
            strict=strict,
        ),
        diag,
    )
