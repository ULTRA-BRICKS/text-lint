"""Message rules: the decision, problem and report formats (Format.*), ports of tg-cli's
validators, and the long-message advisory (Structure.LongUnstructured). They read the raw
message unit only: the `body` field, else `caption`, else the one unit of a file or stdin."""

import io
import json
import re

import pytest
from messages import COMPLIANT, DENSE, PROBLEM_OK, REPORT_OK, input_doc

from text_lint import native
from text_lint.check import check_units
from text_lint.cli import main
from text_lint.engines import native as native_engine
from text_lint.inputs import Meta, parse_input_json, unit_from_text
from text_lint.native import PACK_DIR, load_pack
from text_lint.profiles import load_profile

PACKS = {pack: load_pack(PACK_DIR / pack) for pack in ("formats", "intent", "structure")}
RULES = {rule.id: rule for rules in PACKS.values() for rule in rules}
MESSAGE_RULES = [rule for rules in PACKS.values() for rule in rules]


def found(text, meta, fmt="html", rules=MESSAGE_RULES):
    return native_engine.run([unit_from_text(text, fmt)], meta, rules)


def ids(text, meta, fmt="html"):
    return [f.rule_id for f in found(text, meta, fmt)]


def run_tg_bot(doc):
    units, meta = parse_input_json(doc)
    return check_units(units, meta, load_profile("tg-bot"), enforce="block", from_input_json=True)


def test_message_packs():
    assert {rule.id: rule.kind for rule in MESSAGE_RULES} == {
        "Format.Decision": "format", "Format.Problem": "format", "Format.Report": "format",
        "Intent.MalformedDecision": "intent", "Intent.MalformedProblem": "intent",
        "Intent.MalformedReport": "intent", "Intent.Mixed": "intent",
        "Structure.LongUnstructured": "long-unstructured",
    }
    assert {RULES[f"Format.{v.title()}"].params["validator"] for v in ("decision", "problem", "report")} == {
        "decision", "problem", "report"}
    for rule in MESSAGE_RULES:
        assert rule.profiles == ["tg-bot"]
        assert rule.severity == ("suggestion" if rule.kind == "long-unstructured" else "error")


def test_decision_missing_options():
    body = re.sub(r"<h3>Options</h3><table>[\s\S]*?</table>", "<h3>Options</h3><p>N/A</p>", COMPLIANT)
    (hit,) = found(body, Meta(tag="decision", send_format="html"))
    assert hit.rule_id == "Format.Decision" and hit.severity == "error"
    assert "Options as a real table" in hit.message
    assert "--tag decision" in hit.message
    assert ids(COMPLIANT, Meta(tag="decision", send_format="html")) == []


def test_format_inactive_without_tag():
    for meta in (Meta(), Meta(tag="report"), Meta(tag="problem")):
        assert [i for i in ids("Should we ship A or B?", meta) if i.startswith("Format.")] == []


def test_decision_requires_html():
    for send_format in (None, "plain"):
        (hit,) = found(COMPLIANT, Meta(tag="decision", send_format=send_format), rules=PACKS["formats"])
        assert hit.rule_id == "Format.Decision" and "--format html" in hit.message
    assert ids(COMPLIANT, Meta(tag="decision", send_format="html")) == []
    # A --table send renders its own table: tg-cli rechecks only the body (tg:1653-1656).
    assert ids(COMPLIANT, Meta(tag="decision", table=True)) == []


def test_decision_gated_for_everyone():
    assert ids("ship the thing or not?", Meta(tag="decision", send_format="html")) == ["Format.Decision"]


def test_automated_skips_problem_report():
    bare = "Блокер: CI падает, без токена не починить."
    assert "Format.Problem" in ids(bare, Meta(tag="problem", agent_authored=True))
    assert ids(bare, Meta(tag="problem", automated=True)) == []
    assert ids("Готово, задеплоил.", Meta(tag="report", automated=True)) == []
    # A person's own message is not held to the problem or report format either.
    assert ids(bare, Meta(tag="problem")) == []


def test_problem_and_report_formats_name_what_is_missing():
    (hit,) = found("Готово, задеплоил.", Meta(tag="report", agent_authored=True))
    assert hit.rule_id == "Format.Report"
    assert "Reference" in hit.message and "Verification" in hit.message
    assert ids(REPORT_OK, Meta(tag="report", agent_authored=True)) == []
    assert ids(PROBLEM_OK, Meta(tag="problem", agent_authored=True)) == []


def test_human_report_not_gated(fake_engines, laya_server):
    report = run_tg_bot(input_doc([("body", "Backup complete", "plain")], tag="report"))
    assert [f["rule_id"] for f in report["findings"] if f["rule_id"].startswith(("Format.", "Intent."))] == []
    assert report["intent"]["action"] == "pass"
    (request,) = laya_server.requests
    assert list(request["questions"]) == ["slop"]
    report = run_tg_bot(input_doc([("body", "Backup complete", "plain")], tag="report", agent_authored=True))
    assert "Format.Report" in [f["rule_id"] for f in report["findings"]]


def test_html_problem_passes(fake_engines):
    report = run_tg_bot(input_doc([("body", PROBLEM_OK, "html")], tag="problem", agent_authored=True,
                                  send_format="html"))
    assert [f["rule_id"] for f in report["findings"] if f["rule_id"].startswith(("Format.", "Intent."))] == []
    assert report["intent"]["valid"] == "problem"


def test_message_scope(fake_engines):
    report = run_tg_bot(input_doc(
        [("title", "Which region should we use?", "plain"), ("body", COMPLIANT, "html"),
         ("harness_title", "Should we deploy today?", "plain", False)],
        tag="decision", agent_authored=True, send_format="html"))
    assert [f["rule_id"] for f in report["findings"] if f["rule_id"].startswith(("Format.", "Intent."))] == []
    assert report["intent"]["action"] == "pass"
    assert report["intent"]["valid"] == "decision"


def test_message_unit():
    title, body, caption = (unit_from_text(t, "plain", field=f) for t, f in
                            (("t", "title"), ("b", "body"), ("c", "caption")))
    assert native.message_unit([title, caption, body], from_input_json=True) is body
    assert native.message_unit([title, caption], from_input_json=True) is caption
    assert native.message_unit([title], from_input_json=True) is None
    single = unit_from_text("text", "markdown")
    assert native.message_unit([single], from_input_json=False) is single


def test_message_unit_plain_input(capsys, monkeypatch, fake_engines):
    for argv, stdin in ((["check", "--profile", "tg-bot", "--format", "json"], DENSE),
                        (["check", "--profile", "tg-bot", "--format", "json", "--input-json"],
                         input_doc([("body", DENSE, "plain")]))):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        assert main(argv) == 0
        rules = [f["rule_id"] for f in json.loads(capsys.readouterr().out)["findings"]]
        assert rules == ["Structure.LongUnstructured"], argv


def test_long_unstructured_skips_attachments():
    def long_hits(meta, text=DENSE):
        return [f for f in found(text, meta, fmt="plain") if f.rule_id == "Structure.LongUnstructured"]

    (hit,) = long_hits(Meta(attachments=0))
    assert hit.severity == "suggestion"
    assert long_hits(Meta(attachments=1)) == []
    assert long_hits(Meta(table=True)) == []
    assert long_hits(Meta(send_format="html")) == []
    assert long_hits(Meta(), text=f"Summary\n\n{DENSE}") == []
    assert long_hits(Meta(), text="a" * 499) == []


def test_message_rules_skip_other_fields():
    units, meta = parse_input_json(input_doc([("title", DENSE, "plain"), ("body", "short", "plain")]))
    assert native_engine.run(units, meta, MESSAGE_RULES, from_input_json=True) == []


# What each pack's examples are judged under: the meta of the message they stand for.
EXAMPLE_META = {
    "Format.Decision": Meta(tag="decision", send_format="html"),
    "Format.Problem": Meta(tag="problem", agent_authored=True),
    "Format.Report": Meta(tag="report", agent_authored=True),
    "Structure.LongUnstructured": Meta(),
}


@pytest.mark.parametrize("rule", MESSAGE_RULES, ids=lambda rule: rule.id)
def test_pack_examples_behave(rule):
    meta = EXAMPLE_META.get(rule.id, Meta(agent_authored=True))
    for text in rule.examples["flag"]:
        assert [f.rule_id for f in found(text, meta, rules=[rule])] == [rule.id], text
    for text in rule.examples["pass"]:
        assert found(text, meta, rules=[rule]) == [], text
