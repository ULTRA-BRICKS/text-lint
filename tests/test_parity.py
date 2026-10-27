"""Parity with the gates text-lint replaces: every line of tests/parity/<source>/<corpus>.jsonl,
exported from that tool's own tests, must get its recorded verdict from `text-lint check`.

A line: {"case", "source", "input" (input@1), "expected": "block" | "flag" | "pass" | "error",
"rule": the rule id | null, "dict": fixture path under tests/parity, or null for "no dictionary
configured"}, plus optional "count" (findings in the corpus's scope), "field", "match", "col",
"suggestion" (of the first `rule` finding), "missing" (strings the `rule` finding's message
names), "not_missing" (source labels that must remain absent from that finding), "intent" (keys report["intent"] must have with these values; "intent_action" is
short for {"action": ...}), "laya" (the fake Laya's answers) and "note" (why a line differs
from the source tool). Each corpus checks one scope of rule ids (CORPORA) under one profile:
"block" is exit 1 with a finding of `rule`; "flag" is a finding of `rule` that does not block
(an advisory); "pass" is no finding in the scope; "error" is exit 2 with the dictionary's path
on stderr.
"""

import io
import json
from pathlib import Path

import pytest

from text_lint.cli import main

PARITY = Path(__file__).resolve().parent / "parity"
# corpus -> (profile, the rule ids (prefixes) its tool covers); other rules are not judged.
CORPORA = {
    "tg/dict-gate": ("chat", ("Dict.",)),
    "agent-tools/chat-dictionary-gate": ("chat", ("Dict.",)),
    "tg/cjk-guard": ("chat", ("Script.StrayCjk",)),
    "tg/mixed-script-guard": ("chat", ("Script.MixedScript",)),
    "tg/escalation-format": ("tg-bot", ("Format.Decision",)),
    "tg/intent-gate": ("tg-bot", ("Format.", "Intent.")),
    "tg/message-structure": ("tg-bot", ("Structure.",)),
    "agent-tools/decision-request-format": ("tg-bot", ("Format.", "Intent.")),
}


def cases(corpus):
    path = PARITY / f"{corpus}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def mismatch(case, corpus, capsys, monkeypatch, home, laya_server):
    """None when ``case`` gets its expected verdict, else what went wrong."""
    profile, scope = CORPORA[corpus]
    argv = ["check", "--profile", profile, "--input-json", "--format", "json"]
    if case["dict"] is None:
        monkeypatch.delenv("TEXT_LINT_DICT", raising=False)
        monkeypatch.delenv("TG_DICT_PATH", raising=False)
        monkeypatch.setenv("HOME", str(home))
    else:
        argv += ["--dict", str(PARITY / case["dict"])]
    laya_server.answers = case.get("laya", {})
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(case["input"], ensure_ascii=False)))
    code = main(argv)
    out, err = capsys.readouterr()
    if case["expected"] == "error":
        if code != 2:
            return f"expected exit 2, got {code}"
        return None if str(PARITY / case["dict"]) in err else f"stderr does not name {case['dict']}: {err.strip()}"
    if code == 2:
        return f"exit 2: {err.strip()}"
    report = json.loads(out)
    problem = _intent_mismatch(case, report)
    if problem:
        return problem
    found = [f for f in report["findings"] if f["rule_id"].startswith(scope)]
    if case["expected"] == "pass":
        return None if not found else f"expected no {scope} finding, got {[f['rule_id'] for f in found]}"
    hits = [f for f in found if f["rule_id"] == case["rule"]]
    if not hits or code != (1 if case["expected"] == "block" else 0):
        return f"expected {case['expected']} with {case['rule']}, got exit {code} with {[f['rule_id'] for f in found]}"
    if "count" in case and len(found) != case["count"]:
        return f"expected {case['count']} {scope} findings, got {len(found)}"
    for key in ("field", "match", "col", "suggestion"):
        if key in case and hits[0][key] != case[key]:
            return f"expected {key} {case[key]!r}, got {hits[0][key]!r}"
    absent = [item for item in case.get("missing", []) if item not in hits[0]["message"]]
    if absent:
        return f"message does not name {absent}"
    forbidden = [item for item in case.get("not_missing", []) if item in hits[0]["message"]]
    return f"message must not name {forbidden}" if forbidden else None


def _intent_mismatch(case, report):
    expected = dict(case.get("intent", {}))
    if "intent_action" in case:
        expected["action"] = case["intent_action"]
    got = report.get("intent", {})
    wrong = {key: got.get(key) for key, value in expected.items() if got.get(key) != value}
    return f"intent {wrong} != expected {({key: expected[key] for key in wrong})}" if wrong else None


@pytest.mark.parametrize("corpus", list(CORPORA))
def test_parity(corpus, capsys, monkeypatch, tmp_path, fake_engines, laya_server):
    lines = cases(corpus)
    assert lines
    failures = []
    for i, case in enumerate(lines, 1):
        with monkeypatch.context() as m:
            problem = mismatch(case, corpus, capsys, m, tmp_path, laya_server)
        if problem:
            failures.append(f"line {i} ({case['case']}): {problem}")
    assert failures == []


@pytest.mark.parametrize("corpus", list(CORPORA))
def test_corpus_lines_are_well_formed(corpus):
    for case in cases(corpus):
        assert case["expected"] in ("block", "flag", "pass", "error"), case["case"]
        assert (case["rule"] is not None) == (case["expected"] in ("block", "flag")), case["case"]
        assert case["dict"] is None or (PARITY / case["dict"]).is_file() or case["expected"] == "error"
        assert case["expected"] != "error" or case["dict"] is not None, case["case"]
        if case["expected"] == "error":
            assert case.get("note"), f"{case['case']}: an error line records why"


@pytest.mark.parametrize("case_index", [10, 11, 14, 20, 22, 55])
def test_parity_rejects_source_negative_assertion_regression(case_index, capsys, monkeypatch, tmp_path,
                                                            fake_engines, laya_server):
    from text_lint.native import formats

    case = cases("tg/escalation-format")[case_index]
    label = case["not_missing"][0]
    validate = formats.validate_decision
    # Mutation of the actual validator result: each body is already invalid for unrelated
    # sections, so merely checking block plus its positive subset cannot catch this defect.
    monkeypatch.setattr(formats, "validate_decision", lambda body: [*validate(body), label])
    problem = mismatch(case, "tg/escalation-format", capsys, monkeypatch, tmp_path, laya_server)
    assert problem is not None and "must not name" in problem and label in problem
