"""report@1: verdict, exit code, schema, and one check run over several units."""

import io
import json
from pathlib import Path

import pytest

from text_lint import __version__
from text_lint.check import check_units
from text_lint.cli import main
from text_lint.engines import laya
from text_lint.inputs import Meta, parse_input_json, unit_from_text
from text_lint.profiles import ConfigError, load_profile
from text_lint.report import Finding, build_report, exit_code, verdict

SCHEMA = Path(__file__).resolve().parent.parent / "text_lint" / "schema" / "report-v1.json"


def finding(severity="warning", rule_id="TextLint.Utilize", gate=True, **kw):
    return Finding(rule_id=rule_id, engine=kw.pop("engine", "vale"), severity=severity, line=1, col=1,
                   span=(0, 7), match="utilize", message="m", gate=gate, **kw)


def logged(tmp_path, name):
    path = tmp_path / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_verdict_levels():
    assert verdict([finding("warning")], "warning", "block") == "block"
    assert verdict([finding("warning")], "warning", "warn") == "warn"
    assert verdict([finding("suggestion")], "warning", "block") == "warn"
    assert verdict([finding("error")], "warning", "block") == "block"
    assert verdict([], "warning", "block") == "pass"
    slop = finding("suggestion", rule_id="laya.SlopProbability", engine="laya")
    assert verdict([slop], "suggestion", "block") == "pass"
    assert verdict([finding("error", gate=False)], "warning", "block") == "warn"


def test_exit_codes():
    assert exit_code({"verdict": "pass"}) == 0
    assert exit_code({"verdict": "warn"}) == 0
    assert exit_code({"verdict": "block"}) == 1


def test_finding_as_dict_omits_gate():
    data = finding(field="body").as_dict()
    assert "gate" not in data
    assert data["span"] == [0, 7] and data["field"] == "body" and data["suggestion"] is None


def test_report_matches_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    report = build_report(
        profile=load_profile("tg-bot"), enforce="block",
        findings=[finding(field="body"), finding("suggestion", rule_id="laya.SlopProbability", engine="laya")],
        suppressed=[{"rule_id": "Script.MixedScript", "path": "src/i18n/ru.json", "line": 12,
                     "reason": "brand name spelled this way"}],
        engines={"vale": {"status": "ok", "version": "3.24.0"},
                 "aiw-ru": {"status": "skipped", "reason": "no Russian text"},
                 "laya": {"status": "not run", "reason": "ci runner, no local model"}},
        score=42,
        intent={"kinds": ["decision"], "action": "follow-up", "reason": "r",
                "follow_ups": [{"kind": "decision", "quote": "q"}], "valid": None, "hints": [],
                "laya_p": None},
    )
    jsonschema.validate(report, schema)
    assert report["schema"] == "text-lint/report@1"
    assert report["version"] == __version__
    assert report["profile"] == "tg-bot"
    # tg-bot fails on error; a warning alone does not block.
    assert report["verdict"] == "warn"
    bare = build_report(profile=load_profile("prose"), enforce="warn", findings=[], suppressed=[],
                        engines={}, score=0)
    jsonschema.validate(bare, schema)
    assert "intent" not in bare and bare["verdict"] == "pass"
    broken = dict(bare, verdict="maybe")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, schema)


def test_empty_input_passes(fake_engines, laya_server):
    for name in ("prose", "tg-bot"):
        report = check_units([unit_from_text("  \n", "plain")], Meta(), load_profile(name), enforce="block")
        assert report["verdict"] == "pass" and exit_code(report) == 0
        assert {info["status"] for info in report["engines"].values()} == {"skipped"}, report["engines"]
    assert logged(fake_engines, "vale.log") == [] and logged(fake_engines, "aiwru.log") == []
    assert laya_server.requests == []


def test_fields_batched(fake_engines):
    units, meta = parse_input_json(json.dumps({
        "schema": "text-lint/input@1",
        "fields": [
            {"name": "title", "text": "Стоит отметить заголовок", "format": "plain"},
            {"name": "subagent", "text": "агент", "format": "plain"},
            {"name": "body", "text": "Готово.\nВ современном мире всё иначе.", "format": "plain"},
            {"name": "harness_title", "text": "<b>Стоит отметить</b> тут", "format": "html", "gate": False},
        ],
    }, ensure_ascii=False))
    report = check_units(units, meta, load_profile("prose"), enforce="block")
    (vale_call,) = logged(fake_engines, "vale.log")
    # One process, one file per field: a field's Markdown state cannot reach the next one.
    # Plain and HTML fields are plain text to Vale (`.md.txt`).
    assert [Path(a).name for a in vale_call if Path(a).suffix == ".txt"] == [
        "unit-0.md.txt", "unit-1.md.txt", "unit-2.md.txt", "unit-3.md.txt"]
    assert len(logged(fake_engines, "aiwru.log")) == 1
    where = sorted((f["field"], f["rule_id"], f["line"], f["col"]) for f in report["findings"])
    assert where == [
        ("body", "aiwru.tier1.в-современном-мире", 2, 1),
        ("harness_title", "TextLint.RuStoitOtmetit", 1, 4),
        ("title", "TextLint.RuStoitOtmetit", 1, 1),
    ]
    by_field = {f["field"]: f for f in report["findings"]}
    raw = units[3].raw
    start, end = by_field["harness_title"]["span"]
    # Spans and columns point into the field as sent, through the HTML stripping.
    assert raw[start:end] == "Стоит отметить" == by_field["harness_title"]["match"]
    assert report["verdict"] == "block"


def test_ungated_field_findings_never_block(fake_engines):
    units, meta = parse_input_json(json.dumps({
        "schema": "text-lint/input@1",
        "fields": [{"name": "harness_title", "text": "Great question", "format": "plain", "gate": False},
                   {"name": "body", "text": "Plain body.", "format": "plain"}],
    }))
    report = check_units(units, meta, load_profile("prose"), enforce="block")
    assert [f["rule_id"] for f in report["findings"]] == ["TextLint.Sycophancy"]
    assert report["verdict"] == "warn"


def test_cap_and_optional_engines_in_send_profiles(fake_engines, monkeypatch):
    unit = unit_from_text("Great question. Надеюсь, это поможет\n", "plain")
    report = check_units([unit], Meta(), load_profile("tg-bot"), enforce="block")
    assert {f["severity"] for f in report["findings"]} == {"warning"}
    # tg-bot fails on error, and its Vale/aiw-ru findings are capped at warning.
    assert report["verdict"] == "warn"
    monkeypatch.setenv("TEXT_LINT_VALE", "/nonexistent/vale")
    monkeypatch.setenv("TEXT_LINT_AIWRU", "/nonexistent/aiw-ru")
    report = check_units([unit], Meta(), load_profile("tg-bot"), enforce="block")
    assert report["engines"]["vale"]["status"] == "skipped"
    assert report["engines"]["aiw-ru"]["status"] == "skipped"
    assert "/nonexistent/vale" in report["engines"]["vale"]["reason"]


def test_tg_bot_drops_done_emoji(fake_engines):
    unit = unit_from_text("✅ Done\n", "plain")
    assert check_units([unit], Meta(), load_profile("tg-bot"), enforce="block")["findings"] == []
    rules = [f["rule_id"] for f in check_units([unit], Meta(), load_profile("prose"), enforce="block")["findings"]]
    assert rules == ["TextLint.DoneEmoji"]


def test_laya_mode_ci_asks_a_live_socket_and_needs_ci(fake_engines, laya_server, monkeypatch):
    unit = unit_from_text("Plain text.\n", "plain")
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(ConfigError, match="CI=true"):
        check_units([unit], Meta(), load_profile("pr"), enforce="block", laya_mode="ci")
    monkeypatch.setenv("CI", "true")
    report = check_units([unit], Meta(), load_profile("pr"), enforce="block", laya_mode="ci")
    assert report["engines"]["laya"]["status"] == "ok"
    assert len(laya_server.requests) == 1
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(laya_server.path) + ".gone")
    report = check_units([unit], Meta(), load_profile("pr"), enforce="block", laya_mode="ci")
    assert report["engines"]["laya"] == {"status": "not run", "reason": "ci runner, no local model"}


def test_fail_on_override_and_ignore(fake_engines):
    unit = unit_from_text("We utilize caches.\n", "plain")
    assert check_units([unit], Meta(), load_profile("prose"), enforce="block")["verdict"] == "block"
    assert check_units([unit], Meta(), load_profile("prose"), enforce="block", fail_on="error")["verdict"] == "warn"
    report = check_units([unit], Meta(), load_profile("prose"), enforce="block", ignore=["TextLint"])
    assert report["findings"] == [] and report["verdict"] == "pass"


def test_suppress_runs_before_the_verdict(fake_engines):
    unit = unit_from_text("Great question\n", "plain")
    seen = []

    def suppress(findings):
        seen.extend(findings)
        record = {"rule_id": findings[0].rule_id, "path": None, "line": 1, "reason": "quoted on purpose"}
        meta = Finding(rule_id="TextLint.SuppressionWithoutReason", engine="native", severity="suggestion",
                       line=1, col=1, span=(0, 0), match="", message="m")
        return [], [record], [meta]

    report = check_units([unit], Meta(), load_profile("prose"), enforce="block", suppress=suppress)
    assert [f.rule_id for f in seen] == ["TextLint.Sycophancy"]
    assert report["suppressed"][0]["reason"] == "quoted on purpose"
    assert [f["rule_id"] for f in report["findings"]] == ["TextLint.SuppressionWithoutReason"]
    assert report["verdict"] == "warn"


def test_laya_slop_is_advisory(fake_engines, laya_server):
    laya_server.noul = 0.9
    unit = unit_from_text("Plain text.\n", "plain")
    report = check_units([unit], Meta(), load_profile("issue"), enforce="block")
    assert report["engines"]["laya"]["status"] == "ok"
    assert report["engines"]["laya"]["answers"] == {"slop": 0.9}
    assert [f["rule_id"] for f in report["findings"]] == ["laya.SlopProbability"]
    assert report["verdict"] == "pass"
    (request,) = laya_server.requests
    assert request["message"] == "Plain text.\n" and list(request["questions"]) == ["slop"]


def test_laya_asks_the_profile_questions_in_one_request(fake_engines, laya_server):
    units, meta = parse_input_json(json.dumps({
        "schema": "text-lint/input@1",
        "fields": [{"name": "title", "text": "Title", "format": "plain"},
                   {"name": "body", "text": "Body text.", "format": "plain"}],
    }))
    report = check_units(units, meta, load_profile("tg-bot"), enforce="block")
    # asks_reader serves the intent classifier, which a person's message never meets.
    assert [list(r["questions"]) for r in laya_server.requests] == [["slop"]]
    assert report["engines"]["laya"]["answers"] == {"slop": 0.1}


def test_unreachable_laya_ends_the_run(fake_engines, monkeypatch, tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(directory / "laya.sock"))
    with pytest.raises(laya.LayaUnavailable, match="no server answers"):
        check_units([unit_from_text("Plain text.\n", "plain")], Meta(), load_profile("issue"), enforce="block")


def test_cli_input_json(fake_engines, monkeypatch, capsys):
    document = {"schema": "text-lint/input@1",
                "fields": [{"name": "title", "text": "Fine", "format": "plain"},
                           {"name": "body", "text": "We utilize caches.", "format": "plain"}]}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(document)))
    assert main(["check", "--input-json", "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["profile"] == "prose" and data["enforce"] == "block"
    assert [(f["field"], f["rule_id"]) for f in data["findings"]] == [("body", "TextLint.Utilize")]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(document)))
    assert main(["check", "--input-json", "--enforce", "warn"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("<stdin>[body]:1:4 TextLint.Utilize ")
    monkeypatch.setattr("sys.stdin", io.StringIO('{"fields": []}'))
    assert main(["check", "--input-json"]) == 2
    assert "schema" in capsys.readouterr().err
