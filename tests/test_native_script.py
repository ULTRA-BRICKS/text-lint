"""The `stray-cjk` and `mixed-script` kinds (Script.StrayCjk, Script.MixedScript): ports of
tg-cli's cjk-guard and mixed-script-guard, run on the raw text of every field."""

import json

import pytest

from text_lint.check import check_units
from text_lint.engines import native as native_engine
from text_lint.inputs import Meta, parse_input_json, unit_from_text
from text_lint.native import PACK_DIR, load_pack
from text_lint.profiles import ConfigError, load_profile, names

RULES = {r.id: r for r in load_pack(PACK_DIR / "script")}


def hits(rule_id, *units):
    return native_engine.run(list(units), Meta(), [RULES[rule_id]])


def plain(text, **kw):
    return unit_from_text(text, "plain", **kw)


def test_script_pack():
    assert set(RULES) == {"Script.StrayCjk", "Script.MixedScript"}
    for rule in RULES.values():
        assert rule.severity == "error"
        assert sorted(rule.profiles) == names()
    assert RULES["Script.StrayCjk"].kind == "stray-cjk"
    assert RULES["Script.StrayCjk"].params == {"dominant_share": 0.5, "dominant_min": 3}
    assert RULES["Script.MixedScript"].kind == "mixed-script"


def test_stray_cjk_flags_lone_codepoint():
    (hit,) = hits("Script.StrayCjk", plain("Проверил 的тесты"))
    assert (hit.rule_id, hit.engine, hit.match, hit.line, hit.col, hit.span) == (
        "Script.StrayCjk", "native", "的", 1, 10, (9, 10))
    assert hit.message == "Stray CJK character '的' inside a word; delete it."
    assert hits("Script.StrayCjk", plain("Проверил 的 тесты")) == []


def test_stray_cjk_returns_every_hit():
    found = hits("Script.StrayCjk", plain("ка日eat and mo注re"))
    assert [(h.match, h.col) for h in found] == [("日", 3), ("注", 14)]


def test_cjk_dominant_text_passes():
    assert hits("Script.StrayCjk", plain("日本語のテキストです")) == []
    # Dominance is judged per field, as tg-cli runs the guard on title and caption apart.
    title = plain("日本語のテキスト", field="title")
    body = plain("ка日eat", field="body")
    assert [h.field for h in hits("Script.StrayCjk", title, body)] == ["body"]


def test_mixed_script_allows_slang():
    assert hits("Script.MixedScript", plain("заpushил")) == []
    assert hits("Script.MixedScript", plain("IDшник и PR-ревью")) == []


def test_mixed_script_flags_garbage():
    (hit,) = hits("Script.MixedScript", plain("ок почčesна"))
    assert (hit.match, hit.col, hit.span) == ("почčesна", 4, (3, 11))
    assert hit.message == ("Word 'почčesна' mixes Cyrillic with Latin or other letters (homoglyphs or "
                           "mojibake); retype it.")
    found = hits("Script.MixedScript", plain("ок почčesна и\nещё mіr"))
    assert [(h.match, h.line, h.col) for h in found] == [("почčesна", 1, 4), ("mіr", 2, 5)]


def test_cjk_inside_html_code_blocks():
    doc = {"schema": "text-lint/input@1",
           "fields": [{"name": "body", "text": "Готово: <code>ка日eat</code>", "format": "html"}]}
    units, meta = parse_input_json(json.dumps(doc, ensure_ascii=False))
    report = check_units(units, meta, load_profile("chat"), enforce="block", from_input_json=True)
    (finding,) = report["findings"]
    assert (finding["rule_id"], finding["field"], finding["match"], finding["col"], finding["span"]) == (
        "Script.StrayCjk", "body", "日", 17, [16, 17])
    assert report["verdict"] == "block"


def test_script_kinds_read_raw_markdown_code():
    unit = unit_from_text("The token `ка日eat` was garbled", "markdown")
    (hit,) = hits("Script.StrayCjk", unit)
    assert (hit.match, hit.col) == ("日", 14)


def test_non_gating_field_reports_without_blocking():
    units = [plain("Всё готово", field="body"), plain("ка日eat", field="harness_title", gate=False)]
    report = check_units(units, Meta(), load_profile("chat"), enforce="block")
    assert [(f["rule_id"], f["field"]) for f in report["findings"]] == [("Script.StrayCjk", "harness_title")]
    assert report["verdict"] == "warn"


def test_empty_and_whitespace_fields_pass():
    for rule_id in RULES:
        assert hits(rule_id, plain(""), plain("   \n  ")) == []


@pytest.mark.parametrize("params, problem", [
    ("{dominant_share: 0.5}", "dominant_min"),
    ("{dominant_share: 2, dominant_min: 3}", "dominant_share"),
    ("{dominant_share: 0.5, dominant_min: 0}", "dominant_min"),
    ("{dominant_share: 0.5, dominant_min: 3, extra: 1}", "extra"),
])
def test_stray_cjk_params_are_validated(tmp_path, params, problem):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "Cjk.yml").write_text(
        "id: Test.Cjk\nkind: stray-cjk\nseverity: error\nprofiles: [chat]\nmessage: m\n"
        f"params: {params}\nexamples: {{flag: [a], pass: [b]}}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_pack(pack)
    assert "Cjk.yml" in str(exc.value) and problem in str(exc.value)


def test_mixed_script_takes_no_params(tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "Mix.yml").write_text(
        "id: Test.Mix\nkind: mixed-script\nseverity: error\nprofiles: [chat]\nmessage: m\n"
        "params: {threshold: 2}\nexamples: {flag: [a], pass: [b]}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="threshold"):
        load_pack(pack)


def test_pack_examples_behave():
    for rule in RULES.values():
        for text in rule.examples["flag"]:
            assert [h.rule_id for h in hits(rule.id, plain(text))] == [rule.id], text
        for text in rule.examples["pass"]:
            assert hits(rule.id, plain(text)) == [], text
