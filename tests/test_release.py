import json
from pathlib import Path

from text_lint import __version__, check


def test_selftest_packaged_examples():
    from text_lint.selftest import run
    assert run() == []


def test_selftest_detects_silent_rule(monkeypatch):
    from text_lint import selftest
    from text_lint.native import NativeRule
    rule = NativeRule("Test.Silent", "pattern", "error", ["prose"], "test",
                      {"pattern": "never"}, {"flag": ["hello"], "pass": ["world"]})
    monkeypatch.setattr(selftest, "native_rules", lambda: [rule])
    assert selftest.run("Test.*") == ["Test.Silent"]


def test_public_schema_and_metadata(fake_engines, laya_server):
    import jsonschema

    from text_lint import report
    doc = {"schema": "text-lint/input@1", "fields": [
        {"name": "body", "text": "hello", "format": "plain"},
        {"name": "harness_title", "text": "ка日eat", "format": "html", "gate": False},
    ], "meta": {"agent_authored": False, "automated": True, "attachments": 1, "table": True}}
    result = check(doc, profile="tg-bot")
    schema = Path(report.__file__).parent / "schema" / "report-v1.json"
    jsonschema.validate(result, json.loads(schema.read_text()))
    assert result["version"] == __version__ == "0.4.0"
    assert result["verdict"] == "warn"
    assert any(f["field"] == "harness_title" for f in result["findings"])
