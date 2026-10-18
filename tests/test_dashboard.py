from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from ocs import dashboard
from ocs import fixtures_data as fx
from ocs.cli import main

H = "sha256:" + "ab" * 32
H2 = "sha256:" + "cd" * 32
NON_CLAIMS = [
    {"id": "not_an_attestation", "statement": "Nothing here is an attestation or an approval."},
    {"id": "relative_to_pinned_controller", "statement": "Relative to the pinned revision only."},
]


def tally(c: int, u: int, r: int) -> dict[str, int]:
    return {"confirmed": c, "could_not_check": u, "refuted": r, "n": c + u + r}


def check(cid: str, c: int, u: int, r: int) -> dict[str, Any]:
    verdict = "refuted" if r else ("could_not_check" if u else "confirmed")
    return {
        "check_id": cid,
        "tier": "V1",
        "verdict": verdict,
        "tally": tally(c, u, r),
        "could_not_check_reason": "input_absent" if u else None,
        "findings": [{"code": "wire_x", "locator": "$.a", "detail": "bad member"}] if r else [],
    }


def base_report() -> dict[str, Any]:
    checks = [check("v1.strict", 3, 0, 0), check("v2.chain", 1, 1, 1), check("v4.ledger", 0, 2, 0)]
    return {
        "contract": "verification-report",
        "contract_version": "0.1.0-draft",
        "profile": "verification-report/0.1",
        "report_kind": "verification_report",
        "report_id": "rep-1",
        "generated_at": "2026-10-18T12:00:00+00:00",
        "verifier": {
            "name": "verifier",
            "version": "0.1.0",
            "command": "cognous-verify",
            "source_revision": "0123456789abcdef0123456789abcdef01234567",
            "canonicalization_profile": "p",
        },
        "subject": {
            "canonicalization_profile": "p",
            "inputs": [{"role": "run_record", "name": "r.json", "digest": H, "size_bytes": 10}],
            "inputs_digest": H2,
            "pinned": {
                "controller_revision": "87f02336",
                "contract_versions": {"control-plane-journal": "0.1.0-draft"},
                "reason_registry_digest": H,
            },
        },
        "scope": {"semantic_quality": "not_assessed", "tiers_run": ["V1", "V2"]},
        "checks": checks,
        "summary": {"checks": 3, "confirmed": 1, "could_not_check": 1, "refuted": 1},
        "does_not_prove": copy.deepcopy(NON_CLAIMS),
        "mutation_report": None,
    }


def t_row(cat: str, expected: str, reject: int, accept: int, noop: int = 0, na: int = 0) -> Any:
    n_eff = reject + accept
    return {
        "category": cat,
        "expected_verdict": expected,
        "n_gen": n_eff + noop + na,
        "n_noop": noop,
        "n_na": na,
        "n_eff": n_eff,
        "verdicts": {"reject": reject, "accept": accept, "abstain": 0, "error": 0},
        "detection": {
            "any_code": {"k": reject, "n": n_eff},
            "intended_code": {"k": reject, "n": n_eff},
        },
        "regression": None,
        "false_acceptance": {"k": accept, "n": n_eff},
        "abstained": {"k": 0, "n": n_eff},
        "errored": {"k": 0, "n": n_eff},
        "metric_impact": None,
    }


def seed_row(cat: str, k: int, n: int) -> Any:
    row = t_row(cat, "reject", n, 0)
    row.update(
        detection=None,
        false_acceptance=None,
        abstained=None,
        errored=None,
        regression={"refused_with_intended_code": {"k": k, "n": n}},
    )
    row["verdicts"] = {"reject": n, "accept": 0, "abstain": 0, "error": 0}
    return row


def mutation_report() -> dict[str, Any]:
    doc = base_report()
    doc.update(
        checks=[],
        summary={"checks": 0, "confirmed": 0, "could_not_check": 0, "refuted": 0},
    )
    doc["mutation_report"] = {
        "report_only": True,
        "operator_set_digest": H,
        "seed": 7,
        "frozen_revision": "0123456789abcdef0123456789abcdef01234567",
        "family_t": [
            {
                "corpus_id": "t-seed",
                "provenance": "seed",
                "corpus_digest": H,
                "categories": [seed_row("S1", 5, 5)],
                "scenarios": None,
            },
            {
                "corpus_id": "t-gen",
                "provenance": "generated",
                "corpus_digest": H2,
                "categories": [
                    t_row("W1", "reject", 7, 0),
                    t_row("B", "accept", 0, 6),
                    t_row("X1", "reject", 8, 1),
                    t_row("Q", "not_applicable", 0, 0, na=3),
                ],
                "scenarios": None,
            },
        ],
        "family_s": [
            {
                "corpus_id": "s-ctx",
                "context_only": True,
                "rows": [
                    {"source_id": "src-a", "n_cases": 4, "detected": 3, "survived": 1, "errored": 0}
                ],
            }
        ],
        "clean": [
            {
                "corpus_id": "clean-gen",
                "provenance": "generated",
                "corpus_digest": H2,
                "rows": [{"category": "W1", "false_rejection": {"k": 0, "n": 11}}],
            },
            {
                "corpus_id": "clean-seed",
                "provenance": "seed_valid_fixtures",
                "corpus_digest": H,
                "rows": [{"category": "S1", "false_rejection": {"k": 1, "n": 13}}],
            },
        ],
    }
    return doc


def run_record(run_id: str = "run-1") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "decisions": [
            {
                "decision_id": "d1",
                "result": "hold",
                "reasons": ["grant_scope_mismatch"],
                "decided_at": "2026-10-05T19:00:00+00:00",
            },
        ],
        "attempts": [
            {"attempt_id": "a1", "status": "attempted", "started_at": "2026-10-05T19:00:01+00:00"}
        ],
        "observations": [
            {"observed_at": "2026-10-05T19:00:02+00:00", "state": "absent"},
            {"observed_at": "2026-10-05T18:59:59+00:00", "state": "early"},
        ],
    }


def demo_result() -> dict[str, Any]:
    return {
        "envelope_version": "1",
        "command": "demo",
        "outcome": "pass",
        "exit_code": 0,
        "extensions": {
            "x-facts": {"fact one": True, "fact two": False},
            "x-steps": [{"ok": True}, {"ok": True}, {"ok": False}],
        },
    }


def put(ws: Path, name: str, doc: Any) -> str:
    (ws / name).write_text(json.dumps(doc), encoding="utf-8")
    return name


def run(ws: Path, *inputs: str, out: str = "dash.html") -> int:
    return main(["dashboard", "--out", out, *inputs])


def page_of(ws: Path, *docs: Any) -> str:
    names = [put(ws, f"in{i}.json", d) for i, d in enumerate(docs)]
    assert run(ws, *names) == 0
    return (ws / "dash.html").read_text(encoding="utf-8")


def body_text(page: str) -> str:
    page = re.sub(r"<style>.*?</style>", "", page, flags=re.S)
    page = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    page = re.sub(r"<meta[^>]*>", "", page)
    return re.sub(r"<[^>]+>", " ", page)


# ---------------------------------------------------------------- content


def test_trace_report_shows_three_valued_bars_with_k_over_n(ws: Path) -> None:
    page = page_of(ws, base_report())
    assert "v1.strict" in page and "v2.chain" in page
    assert "3 confirmed" in page and "1 refuted" in page and "of 3 items" in page
    assert "s-clear" in page and "s-unsure" in page and "s-found" in page
    assert "1 finding(s)" in page and "bad member" in page
    assert 'aria-label="v2.chain items"' in page
    for word in ("found a problem", "found nothing wrong", "could not check", "verifier crashed"):
        assert word in page
    assert "relative_to_pinned_controller" in page


def test_mutation_report_keeps_populations_apart(ws: Path) -> None:
    page = page_of(ws, mutation_report())
    assert "Family T: <code>t-seed</code>" in page and "Family T: <code>t-gen</code>" in page
    assert "Clean false rejection" in page
    assert "Clean corpus <code>clean-gen</code>" in page
    assert "Clean corpus <code>clean-seed</code>" in page
    assert "context only" in page and "<code>src-a</code>" in page
    assert "5/5 refused with the intended code" in page
    assert "7/7 rejected" in page and "6/6 accepted" in page and "8/9 rejected" in page
    assert "1 other outcome(s)" in page
    assert "0/11 rejected" in page and "1/13 rejected" in page
    # nothing is pooled: no denominator that sums two corpora or two populations appears
    for pooled in ("/16", "/24", "/20", "/30", "/17"):
        assert pooled not in body_text(page).replace(" ", "")


def test_zero_denominator_renders_na(ws: Path) -> None:
    page = page_of(ws, mutation_report())
    assert "n/a" in page
    assert "0/0" not in page
    assert 'aria-label="Q: 0 reject, 0 accept, 0 abstain, 0 error of 0 effective: n/a"' in page


def test_zero_denominator_in_clean_corpus(ws: Path) -> None:
    doc = mutation_report()
    doc["mutation_report"]["clean"][0]["rows"][0]["false_rejection"] = {"k": 0, "n": 0}
    page = page_of(ws, doc)
    assert "n/a rejected" in page and "0/0" not in page


def test_table_view_exists_for_every_chart(ws: Path) -> None:
    page = page_of(ws, mutation_report(), put_rr(ws))
    assert page.count('<details class="tview">') >= 5
    assert "<caption>t-gen: counts per category</caption>" in page
    assert "<caption>Recorded events in strip order</caption>" in page


def put_rr(ws: Path) -> dict[str, Any]:
    return run_record()


def test_identity_digests_shortened_with_full_value_kept(ws: Path) -> None:
    page = page_of(ws, base_report())
    assert f'data-full="{H}"' in page
    assert f'<code class="full">{H}</code>' in page
    assert "sha256:abababab…abab" in page
    assert "87f02336" in page


def test_run_record_timeline_orders_events(ws: Path) -> None:
    page = page_of(ws, run_record())
    rows = re.findall(r"<tr><td class=\"num\">(\d)</td><td>(\w+)</td><td>(\w+)</td>", page)
    assert rows == [
        ("1", "observation", "early"),
        ("2", "decision", "hold"),
        ("3", "attempt", "attempted"),
        ("4", "observation", "absent"),
    ]
    assert "grant_scope_mismatch" in page and "<svg" in page


def test_demo_result_is_relayed_with_k_over_n(ws: Path) -> None:
    page = page_of(ws, demo_result())
    assert "1/2 facts held" in page and "2/3 steps ended as expected" in page
    assert "outcome <b>pass</b>" in page and "does not assess it" in page


def test_evidence_pack_panel(ws: Path) -> None:
    pack = json.loads(fx.read_fixture("evidence-pack.json"))
    page = page_of(ws, pack)
    assert "email_send" in page and "Recorded as blocked" in page


def test_demo_directory_input(ws: Path) -> None:
    root = ws / "demo"
    (root / "control" / "email_send" / "out").mkdir(parents=True)
    (root / "outputs").mkdir()
    (root / "demo-result.json").write_text(json.dumps(demo_result()))
    (root / "control" / "email_send" / "out" / "bounded-run-record.json").write_text(
        json.dumps(run_record())
    )
    (root / "outputs" / "evidence-pack.json").write_bytes(fx.read_fixture("evidence-pack.json"))
    assert run(ws, "demo") == 0
    page = (ws / "dash.html").read_text(encoding="utf-8")
    assert "demo/demo-result.json" in page
    assert "demo/control/email_send/bounded-run-record" in page
    assert "demo/outputs/evidence-pack.json" in page


def test_boundary_and_no_verdict_statements_are_on_the_page(ws: Path) -> None:
    page = page_of(ws, base_report())
    assert "This page reads reports. It adds no verdict." in page
    assert "What this page does not prove" in page
    assert "not an attestation" in page
    assert "Nothing here is an attestation or an approval." in page  # the report's own, quoted


# ---------------------------------------------------------------- claim rules


FORBIDDEN = re.compile(
    r"\b(score|scores|grade|graded|rating|overall|safe|unsafe|certif\w*|compliant|compliance|"
    r"passed|failed|pass rate|production[- ]ready|trustworthy|approved)\b",
    re.I,
)


@pytest.mark.parametrize("builder", [base_report, mutation_report, run_record, demo_result])
def test_no_composite_score_grade_or_traffic_light(ws: Path, builder: Any) -> None:
    text = body_text(page_of(ws, builder()))
    assert not FORBIDDEN.findall(text), FORBIDDEN.findall(text)
    assert "%" not in text, "a percentage would be a bare rate"
    assert not re.search(r"\d\s*(percent|per cent)", text, re.I)


def test_no_composite_in_source_and_css(ws: Path) -> None:
    page = page_of(ws, mutation_report())
    assert "%" not in re.sub(r"<style>.*?</style>", "", page, flags=re.S).replace("100%", "")
    for color in ("#0ca30c", "#d03b3b", "#fab219"):  # the reserved status palette is not used
        assert color not in page.lower()
    assert "<progress" not in page and "<meter" not in page and "conic-gradient" not in page


def test_every_bar_state_has_a_pattern_and_a_word(ws: Path) -> None:
    css = dashboard._CSS
    for state in ("clear", "found", "unsure", "crash"):
        rule = re.search(rf"\.s-{state}\{{(.*?)\}}", css)
        assert rule is not None
    for state in ("found", "unsure", "crash"):
        rule = re.search(rf"\.s-{state}\{{(.*?)\}}", css)
        assert rule is not None and "gradient" in rule.group(1)
    for state in ("found a problem", "found nothing wrong", "could not check", "verifier crashed"):
        assert state in page_of(ws, base_report())


# ---------------------------------------------------------------- self-containment, escaping


def test_page_is_self_contained_and_script_is_hash_pinned(ws: Path) -> None:
    page = page_of(ws, mutation_report(), run_record())
    assert "http://" not in page and "https://" not in page
    assert not re.search(r"\b(src|href)\s*=\s*[\"'](?!#)", page)
    assert "default-src 'none'" in page and "style-src 'unsafe-inline'" in page
    assert page.count("<script>") == 1
    m = re.search(r"script-src 'sha256-([A-Za-z0-9+/=]+)'", page)
    assert m is not None
    import base64
    import hashlib

    script = re.search(r"<script>(.*?)</script>", page, re.S)
    assert script is not None
    assert base64.b64encode(hashlib.sha256(script.group(1).encode()).digest()).decode() == m.group(
        1
    )
    code = script.group(1)
    for banned in ("innerHTML", "outerHTML", "fetch(", "XMLHttpRequest", "eval(", "WebSocket"):
        assert banned not in code
    assert "@import" not in page and "url(" not in page


def test_page_declares_light_and_dark_and_print_and_focus(ws: Path) -> None:
    page = page_of(ws, base_report())
    assert "prefers-color-scheme:dark" in page and "@media print" in page
    assert ":focus-visible" in page and "viewport" in page and 'lang="en"' in page


EVIL = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'&"


def evil_everywhere() -> list[dict[str, Any]]:
    r = base_report()
    r["report_id"] = EVIL
    r["checks"][0]["check_id"] = EVIL
    r["checks"][1]["findings"][0].update(code=EVIL, locator=EVIL, detail=EVIL)
    r["verifier"]["name"] = EVIL
    r["subject"]["inputs"][0]["name"] = EVIL
    r["does_not_prove"][0]["statement"] = EVIL
    r["does_not_prove"][0]["id"] = EVIL
    r["subject"]["pinned"]["controller_revision"] = EVIL
    m = mutation_report()
    t = m["mutation_report"]
    t["family_t"][1]["corpus_id"] = EVIL
    t["family_t"][1]["categories"][0]["category"] = EVIL
    t["clean"][0]["corpus_id"] = EVIL + "c"
    t["clean"][0]["rows"][0]["category"] = EVIL
    t["family_s"][0]["corpus_id"] = EVIL + "s"
    t["family_s"][0]["rows"][0]["source_id"] = EVIL
    rr = run_record(EVIL)
    rr["decisions"][0]["result"] = EVIL
    rr["decisions"][0]["reasons"] = [EVIL]
    rr["attempts"][0]["status"] = EVIL
    pack = json.loads(fx.read_fixture("evidence-pack.json"))
    pack["title"] = EVIL
    pack["blocked_actions"][0]["reason_summary"] = EVIL
    d = demo_result()
    d["extensions"]["x-facts"] = {EVIL: True}
    d["outcome"] = EVIL
    return [r, m, rr, pack, d]


def test_hostile_strings_are_escaped_everywhere(ws: Path) -> None:
    page = page_of(ws, *evil_everywhere())
    assert page.count("<script") == 1, "only the page's own script may exist"
    assert "<img" not in page and "onerror=alert(2)>" not in page
    assert "&quot;" in page and "src=x onerror" not in page.replace("&lt;img src=x onerror", "")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    # no attribute value was broken out of
    for attr_value in re.findall(r'(?:data-full|data-label|aria-label|title)="([^"]*)"', page):
        assert "<" not in attr_value and "\n" not in attr_value


def test_control_characters_are_stripped(ws: Path) -> None:
    doc = base_report()
    doc["checks"][0]["check_id"] = "id\x1b[2J\x00\x07x"
    page = page_of(ws, doc)
    assert "id[2Jx" in page
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", page)


def test_render_is_byte_deterministic(ws: Path, tmp_path: Path) -> None:
    names = [
        put(ws, "a.json", base_report()),
        put(ws, "b.json", mutation_report()),
        put(ws, "c.json", run_record()),
    ]
    assert run(ws, *names, out="one.html") == 0
    assert run(ws, *names, out="two.html") == 0
    other = tmp_path / "elsewhere"
    other.mkdir()
    for n in names:
        (other / n).write_bytes((ws / n).read_bytes())
    import os

    cwd = os.getcwd()
    os.chdir(other)
    try:
        assert main(["dashboard", "--out", "three.html", *names]) == 0
    finally:
        os.chdir(cwd)
    a = (ws / "one.html").read_bytes()
    assert a == (ws / "two.html").read_bytes() == (other / "three.html").read_bytes()
    assert b"2026-10-18" not in a.replace(b"2026-10-18T12:00:00+00:00", b"")  # no render time


# ---------------------------------------------------------------- refusals


def mutate(fn: Any, base: Any = base_report) -> dict[str, Any]:
    doc = base()
    fn(doc)
    return doc  # type: ignore[no-any-return]


BAD_DOCS = {
    "tally-not-summing": mutate(lambda d: d["checks"][0]["tally"].update(n=9)),
    "verdict-not-derived": mutate(lambda d: d["checks"][0].update(verdict="refuted")),
    "summary-mismatch": mutate(lambda d: d["summary"].update(confirmed=3)),
    "undeclared-score-member": mutate(lambda d: d.update(score=1)),
    "bad-digest": mutate(lambda d: d["subject"].update(inputs_digest="sha256:xyz")),
    "wrong-version": mutate(lambda d: d.update(contract_version="1.0.0")),
    "semantic-quality-claimed": mutate(lambda d: d["scope"].update(semantic_quality="certified")),
    "empty-non-claims": mutate(lambda d: d.update(does_not_prove=[])),
    "bool-as-int": mutate(lambda d: d["checks"][0]["tally"].update(n=True, confirmed=1)),
    "finding-on-confirmed": mutate(
        lambda d: d["checks"][0].update(findings=[{"code": "a", "locator": "b", "detail": "c"}])
    ),
    "empty-report": mutate(
        lambda d: d.update(
            checks=[], summary={"checks": 0, "confirmed": 0, "could_not_check": 0, "refuted": 0}
        )
    ),
    "k-above-n": mutate(
        lambda d: d["mutation_report"]["clean"][0]["rows"][0].update(
            false_rejection={"k": 12, "n": 11}
        ),
        mutation_report,
    ),
    "bare-number-fraction": mutate(
        lambda d: d["mutation_report"]["clean"][0]["rows"][0].update(false_rejection=0.5),
        mutation_report,
    ),
    "verdicts-not-summing": mutate(
        lambda d: d["mutation_report"]["family_t"][1]["categories"][0]["verdicts"].update(accept=4),
        mutation_report,
    ),
    "n-eff-wrong": mutate(
        lambda d: d["mutation_report"]["family_t"][1]["categories"][0].update(n_eff=99),
        mutation_report,
    ),
    "seed-with-detection": mutate(
        lambda d: d["mutation_report"]["family_t"][0]["categories"][0].update(
            detection={"any_code": {"k": 5, "n": 5}, "intended_code": {"k": 5, "n": 5}}
        ),
        mutation_report,
    ),
    "family-s-not-context-only": mutate(
        lambda d: d["mutation_report"]["family_s"][0].update(context_only=False), mutation_report
    ),
    "family-s-not-summing": mutate(
        lambda d: d["mutation_report"]["family_s"][0]["rows"][0].update(survived=3),
        mutation_report,
    ),
    "duplicate-corpus-id": mutate(
        lambda d: d["mutation_report"]["clean"][0].update(corpus_id="t-gen"), mutation_report
    ),
    "duplicate-category": mutate(
        lambda d: d["mutation_report"]["family_t"][1]["categories"].append(
            t_row("W1", "reject", 1, 0)
        ),
        mutation_report,
    ),
    "report-only-false": mutate(
        lambda d: d["mutation_report"].update(report_only=False), mutation_report
    ),
    "run-record-bad-time": mutate(
        lambda d: d["decisions"][0].update(decided_at="yesterday"), run_record
    ),
    "demo-facts-not-bool": mutate(
        lambda d: d["extensions"].update({"x-facts": {"a": "yes"}}), demo_result
    ),
}


@pytest.mark.parametrize("name", sorted(BAD_DOCS))
def test_malformed_input_is_refused_and_nothing_is_written(
    ws: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    good = put(ws, "good.json", base_report())
    bad = put(ws, "bad.json", BAD_DOCS[name])
    assert main(["--format", "json", "dashboard", "--out", "d.html", good, bad]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["outcome"] == "invalid"
    assert body["reason_codes"][0] in {"OCS_INPUT_INVALID", "OCS_KIND_UNREGISTERED"}
    assert not (ws / "d.html").exists(), "no partial page when one input is refused"
    assert json.loads((ws / "ci-diagnostics.json").read_text())["exit_code"] == 2


def test_wrong_contract_version_is_unregistered(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = put(ws, "bad.json", BAD_DOCS["wrong-version"])
    assert main(["--format", "json", "dashboard", "--out", "d.html", bad]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["OCS_KIND_UNREGISTERED"]


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[1]",
        b"\xff\xfe",
        b'{"contract": "verification-report", "contract": "x"}',
        b'{"contract": "verification-report", "n": NaN}',
        b'{"unrelated": true}',
    ],
)
def test_unparseable_or_unknown_input_is_refused(ws: Path, raw: bytes) -> None:
    (ws / "x.json").write_bytes(raw)
    assert run(ws, "x.json") == 2
    assert not (ws / "dash.html").exists()


def test_no_input_is_refused(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--format", "json", "dashboard", "--out", "d.html"]) == 2
    assert "no input" in json.loads(capsys.readouterr().out)["messages"][0]


def test_directory_without_demo_output_is_refused(ws: Path) -> None:
    (ws / "empty").mkdir()
    assert run(ws, "empty") == 2


def test_symlinked_demo_file_is_refused(ws: Path) -> None:
    real = ws / "real.json"
    real.write_text(json.dumps(demo_result()))
    (ws / "d").mkdir()
    (ws / "d" / "demo-result.json").symlink_to(real)
    assert run(ws, "d") == 2


def test_too_many_events_are_refused(ws: Path) -> None:
    doc = run_record()
    doc["observations"] = [
        {"observed_at": "2026-10-05T19:00:00+00:00", "state": "x"}
    ] * dashboard.MAX_EVENTS
    assert run(ws, put(ws, "big.json", doc)) == 2


# ---------------------------------------------------------------- contract: paths, envelope


def test_envelope_on_success(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    name = put(ws, "r.json", base_report())
    assert main(["--format", "json", "dashboard", "--out", "d.html", name]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["envelope_version"] == "1" and body["command"] == "dashboard"
    assert body["outcome"] == "pass" and body["exit_code"] == 0 and body["reason_codes"] == []
    assert "verdict_source" not in body
    assert body["does_not_prove"] and any("contract" in s for s in body["does_not_prove"])
    assert body["subject"]["kinds"] == {"verification-report": 1}
    assert not (ws / "ci-diagnostics.json").exists()


def test_writes_only_the_named_file(ws: Path) -> None:
    names = [put(ws, "r.json", base_report()), put(ws, "m.json", mutation_report())]
    before = {p.name for p in ws.iterdir()}
    assert run(ws, *names, out="sub/dash.html") == 0
    assert {p.name for p in ws.iterdir()} == before | {"sub"}
    assert [p.name for p in (ws / "sub").iterdir()] == ["dash.html"]


def test_output_outside_workspace_is_refused(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    name = put(ws, "r.json", base_report())
    assert main(["--format", "json", "dashboard", "--out", "/etc/dash.html", name]) == 2
    assert json.loads(capsys.readouterr().out)["reason_codes"] == ["OCS_PATHS"]


def test_input_aliasing_the_output_is_refused(ws: Path) -> None:
    name = put(ws, "r.json", base_report())
    before = (ws / name).read_bytes()
    assert run(ws, name, out=name) == 2
    assert (ws / name).read_bytes() == before


def test_crlf_in_paths_is_refused(ws: Path) -> None:
    put(ws, "r.json", base_report())
    assert run(ws, "r.json\n") == 2
    assert main(["dashboard", "--out", "a\nb.html", "r.json"]) == 2


def test_missing_out_is_a_usage_error(ws: Path) -> None:
    assert main(["dashboard", put(ws, "r.json", base_report())]) == 2


def test_mutation_report_counts_are_only_what_the_report_says(ws: Path) -> None:
    doc = mutation_report()
    doc["mutation_report"]["family_t"][1]["categories"][2] = t_row("X1", "reject", 8, 1)
    page = page_of(ws, doc)
    assert "8/9 rejected" in page
    assert "9/9" not in page
