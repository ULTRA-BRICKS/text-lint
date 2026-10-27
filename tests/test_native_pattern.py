"""The native engine and its `pattern` kind: DICT.json regex semantics, script gating over the
gating fields, normalized text (HTML stripped, Markdown code masked), and rule packs."""

import re

import pytest

from text_lint.engines import native as native_engine
from text_lint.inputs import Meta, unit_from_text
from text_lint.native import KINDS, NativeRule, load_pack
from text_lint.native.pattern import compile_pattern, unicode_boundary_pattern
from text_lint.profiles import ConfigError


def rule(pattern, flags="iu", **params):
    return NativeRule(id="Test.Rule", kind="pattern", severity="error", profiles=["chat"],
                      message="'{match}' is banned", params={"pattern": pattern, "flags": flags, **params},
                      examples={}, replacement="бенч", why=None)


def run(r, *units):
    return native_engine.run(list(units), Meta(), [r])


def test_pattern_is_registered():
    assert "pattern" in KINDS


def test_boundary_rewrite():
    r = rule(r"\bскаме[йе]")
    (hit,) = run(r, unit_from_text("на скамейке", "plain"))
    assert (hit.rule_id, hit.engine, hit.severity, hit.match) == ("Test.Rule", "native", "error", "скамей")
    assert (hit.line, hit.col, hit.span) == (1, 4, (3, 9))
    assert hit.message == "'скамей' is banned"
    assert hit.suggestion == "бенч"
    assert run(r, unit_from_text("наскамейке", "plain")) == []


def test_unicode_boundary_port_keeps_class_backspace_and_escapes():
    assert unicode_boundary_pattern("[\\b]") == "[\\b]"
    assert unicode_boundary_pattern("a\\\\b") == "a\\\\b"
    assert unicode_boundary_pattern("\\B") == "\\B"
    sel = compile_pattern(r"\bсел\b", "iu")
    assert sel.search("весело") is None
    assert sel.search("он сел.") is not None
    assert sel.search("сел") is not None


def test_flags_follow_tg():
    # g and y are dropped, u is forced, i/m/s map to Python's flags; tg-cli collects the flags
    # in a Set, so a repeated flag is accepted there and here.
    assert compile_pattern("а", "giuy").flags & re.IGNORECASE
    assert compile_pattern("а", "ii").flags & re.IGNORECASE
    assert compile_pattern("а", "m").flags & re.MULTILINE
    assert compile_pattern("а", "s").flags & re.DOTALL
    with pytest.raises(ValueError, match="unknown flag 'x'"):
        compile_pattern("а", "ix")


def test_control_character_in_pattern_is_refused_with_hint():
    with pytest.raises(ValueError, match=r"U\+0008.*backspace"):
        compile_pattern("\bпочтил\b", "iu")


def test_every_occurrence_and_no_empty_match():
    hits = run(rule("скамейк[а-яё]*", "giu"), unit_from_text("скамейка и скамейки", "plain"))
    assert [h.match for h in hits] == ["скамейка", "скамейки"]
    assert run(rule("x*"), unit_from_text("abc", "plain")) == []


def test_only_if_cyrillic():
    r = rule(r"\bfork\b", only_if_script="cyrillic")
    assert run(r, unit_from_text("Create a fork", "plain")) == []
    title = unit_from_text("Проверка", "plain", field="title")
    body = unit_from_text("Create a fork", "plain", field="body")
    (hit,) = run(r, title, body)
    assert (hit.field, hit.match) == ("body", "fork")


def test_only_if_cyrillic_ignores_non_gating_field():
    r = rule(r"\bfork\b", only_if_script="cyrillic")
    body = unit_from_text("Create a fork", "plain", field="body")
    title = unit_from_text("Проверка", "plain", field="harness_title", gate=False)
    assert run(r, body, title) == []
    gating_title = unit_from_text("Проверка", "plain", field="harness_title")
    (hit,) = run(r, body, gating_title)
    assert (hit.field, hit.match, hit.gate) == ("body", "fork", True)


def test_non_gating_field_is_judged_on_its_own_text():
    r = rule(r"\bfork\b", only_if_script="cyrillic")
    body = unit_from_text("Create a fork", "plain", field="body")
    title = unit_from_text("Проверка fork", "plain", field="harness_title", gate=False)
    (hit,) = run(r, body, title)
    assert (hit.field, hit.gate, hit.col) == ("harness_title", False, 10)


def test_script_gate_reads_raw_text():
    # tg-cli judges only_if_cyrillic on the raw field: Cyrillic quoted in <code> still counts.
    r = rule(r"\bland(?:ing|ed|s)?\b", only_if_script="cyrillic")
    title = unit_from_text("Landing done", "plain", field="title")
    body = unit_from_text("see <code>правку</code> for details", "html", field="body")
    (hit,) = run(r, title, body)
    assert (hit.field, hit.match) == ("title", "Landing")


def test_html_code_masked():
    r = rule("скаме[йе]к[а-яё]*")
    assert run(r, unit_from_text("<code>&lt;скамейка&gt;</code>", "html", field="body")) == []
    raw = "Это &lt;скамейка&gt;"
    (hit,) = run(r, unit_from_text(raw, "html", field="body"))
    assert hit.match == "скамейка"
    assert (hit.line, hit.col, hit.span) == (1, 9, (8, 16))
    assert raw[hit.span[0]:hit.span[1]] == "скамейка"


def test_html_tags_and_entities_cannot_hide_a_word():
    r = rule(r"\bпочтил\b")
    raw = "я почт<b>ил</b> баг"
    (hit,) = run(r, unit_from_text(raw, "html"))
    assert hit.match == "почтил"
    assert (hit.col, hit.span) == (3, (2, 11))
    (hit,) = run(r, unit_from_text("я почт&#1080;л", "html"))
    assert hit.match == "почтил"


def test_markdown_code_and_urls_masked_plain_as_written():
    r = rule("скаме[йе]к[а-яё]*")
    text = "`скамейка` и https://example.com/скамейка"
    assert run(r, unit_from_text(text, "markdown")) == []
    assert [h.match for h in run(r, unit_from_text(text, "plain"))] == ["скамейка", "скамейка"]


def test_markdown_match_never_spans_masked_code():
    # Masking blanks the code, but a pattern may still match across the blanks.
    r = rule(r"скамейк[а-яё]*.*запасных")
    text = "скамейка `x` запасных"
    assert run(r, unit_from_text(text, "markdown")) == []
    assert [h.match for h in run(r, unit_from_text(text, "plain"))] == [text]


def test_findings_in_unit_order_then_offset():
    r = rule("скамь[яюе]")
    hits = run(r, unit_from_text("скамья", "plain", field="title"),
               unit_from_text("скамью и\nскамье", "plain", field="body"))
    assert [(h.field, h.line, h.col) for h in hits] == [("title", 1, 1), ("body", 1, 1), ("body", 2, 1)]


PACK_RULE = """\
id: Test.Bench
kind: pattern
severity: warning
profiles: [chat, prose]
message: "'{match}' is furniture"
params: {pattern: "скаме[йе]", flags: iu}
examples:
  flag: ["на скамейке"]
  pass: ["бенч"]
"""


def write_pack(tmp_path, text, name="Bench.yml"):
    pack = tmp_path / "pack"
    pack.mkdir(exist_ok=True)
    (pack / name).write_text(text, encoding="utf-8")
    return pack


def test_load_pack(tmp_path):
    (r,) = load_pack(write_pack(tmp_path, PACK_RULE))
    assert r == NativeRule(id="Test.Bench", kind="pattern", severity="warning", profiles=["chat", "prose"],
                           message="'{match}' is furniture", params={"pattern": "скаме[йе]", "flags": "iu"},
                           examples={"flag": ["на скамейке"], "pass": ["бенч"]}, replacement=None, why=None)


@pytest.mark.parametrize("change, problem", [
    (("  pass: [\"бенч\"]\n", ""), "pass"),
    (("kind: pattern", "kind: telepathy"), "telepathy"),
    (("скаме[йе]", "(unclosed"), "invalid regex"),
    (("[chat, prose]", "[chat, novel]"), "novel"),
    (("severity: warning", "severity: fatal"), "fatal"),
    (("flags: iu}", "flags: iu, extra: 1}"), "extra"),
])
def test_load_pack_rejects_invalid_rule_naming_the_file(tmp_path, change, problem):
    pack = write_pack(tmp_path, PACK_RULE.replace(*change))
    with pytest.raises(ConfigError) as exc:
        load_pack(pack)
    assert "Bench.yml" in str(exc.value)
    assert problem in str(exc.value)


def test_load_pack_rejects_broken_yaml_and_duplicate_ids(tmp_path):
    pack = write_pack(tmp_path, "id: [unclosed\n")
    with pytest.raises(ConfigError, match="Bench.yml"):
        load_pack(pack)
    pack = write_pack(tmp_path, PACK_RULE)
    write_pack(tmp_path, PACK_RULE, name="Again.yml")
    (pack / "Bench.yml").write_text(PACK_RULE, encoding="utf-8")
    with pytest.raises(ConfigError, match="Test.Bench"):
        load_pack(pack)
