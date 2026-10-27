"""input@1 parsing, units and their position mapping, HTML stripping."""

import json
from pathlib import Path

import pytest

from text_lint.inputs import InputError, Meta, Unit, parse_input_json, unit_from_text
from text_lint.text import strip_html

SCHEMA = Path(__file__).resolve().parent.parent / "text_lint" / "schema" / "input-v1.json"


def doc(fields, meta=None, **extra):
    data = {"schema": "text-lint/input@1", "fields": fields, **extra}
    if meta is not None:
        data["meta"] = meta
    return json.dumps(data, ensure_ascii=False)


def test_parse_input_json():
    units, meta = parse_input_json(doc(
        [{"name": "title", "text": "Fix the cache", "format": "plain"},
         {"name": "body", "text": "<b>Готово</b> &amp; всё", "format": "html", "gate": False}],
        {"tag": "decision", "agent_authored": True, "send_format": "html", "attachments": 2},
    ))
    assert [u.field for u in units] == ["title", "body"]
    assert meta == Meta(tag="decision", agent_authored=True, automated=False, send_format="html",
                        attachments=2, table=False)
    title, body = units
    assert title.gate is True and title.text == title.raw == "Fix the cache"
    assert body.gate is False
    assert body.raw == "<b>Готово</b> &amp; всё"
    assert body.text == "Готово & всё"
    assert body.format == "html"


@pytest.mark.parametrize("raw", [
    json.dumps({"fields": [{"name": "body", "text": "x", "format": "plain"}]}),  # no schema key
    doc([{"name": "body", "text": "x", "format": "rtf"}]),
    doc([{"name": "body", "text": 1, "format": "plain"}]),
    doc([{"text": "x", "format": "plain"}]),
    doc([{"name": "body", "text": "x", "format": "plain", "gate": "no"}]),
    doc([{"name": "body", "text": "x", "format": "plain", "colour": "red"}]),
    doc([], {"tag": 3}),
    doc([], {"send_format": "rtf"}),
    doc([], {"attachments": -1}),
    doc([], {"agent_authored": "yes"}),
    doc([], {"surprise": True}),
    doc([], None, extra=1),
    json.dumps({"schema": "text-lint/input@2", "fields": []}),
    json.dumps([]),
    "not json",
])
def test_invalid_input_raises(raw):
    with pytest.raises(InputError):
        parse_input_json(raw)


def test_meta_defaults():
    units, meta = parse_input_json(doc([]))
    assert units == []
    assert meta == Meta(tag=None, agent_authored=False, automated=False, send_format=None)


def test_example_documents_match_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    good = json.loads(doc([{"name": "body", "text": "x", "format": "html", "gate": False}],
                          {"tag": "decision", "agent_authored": True, "automated": False,
                           "send_format": "html", "attachments": 0, "table": False}))
    jsonschema.validate(good, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(json.loads(doc([{"name": "body", "text": "x", "format": "rtf"}])), schema)


def test_unit_from_text_formats():
    plain = unit_from_text("a <b>b</b>", "plain")
    assert plain.text == plain.raw == "a <b>b</b>" and plain.field is None and plain.gate
    md = unit_from_text("Use `x` here", "markdown")
    # Markdown reaches the engines as written; code and URLs are dropped from findings later.
    assert md.text == md.raw
    html = unit_from_text("a <b>b</b>", "html")
    assert html.text == "a b"


def test_unit_position_spaces():
    raw = "<p>a</p><code>ка日eat</code>"
    text, norm_to_raw = strip_html(raw)
    # As if the field were quoted in a file from line 7, column 3 on, one line.
    raw_map = [(7, 3 + i) for i in range(len(raw))]
    unit = Unit(text=text, raw=raw, format="html", field="body", path=None,
                raw_map=raw_map, norm_to_raw=norm_to_raw)
    assert unit.position(raw.index("日"), space="raw") == (7, 3 + raw.index("日"))
    assert unit.position(text.index("a"), space="text") == (7, 3 + raw.index("a"))
    # One past the end (an empty match at the end) stays in range.
    assert unit.position(len(raw), space="raw") == (7, 3 + len(raw))
    assert unit.position(len(text), space="text") == (7, 3 + len(raw))


def test_unit_position_without_maps():
    unit = Unit(text="ab\ncd", raw="ab\ncd", format="plain", field=None, path=None, line_offset=4)
    assert unit.position(0, space="text") == (5, 1)
    assert unit.position(4, space="raw") == (6, 2)
    assert unit.position(5, space="raw") == (6, 3)


def test_raw_span_covers_entities_not_tags():
    unit = unit_from_text("a&#8212;b <b>Готово</b>&#33; x&amp;y &bogus; &lt;b&gt;", "html")

    def raw_of(match):
        start = unit.text.index(match)
        s, e = unit.raw_span(start, start + len(match))
        return unit.raw[s:e]

    assert raw_of("—") == "&#8212;"
    assert raw_of("a—b") == "a&#8212;b"
    # A match ending before a removed tag stops there; one ending on an entity covers it.
    assert raw_of("Готово") == "Готово"
    assert raw_of("Готово!") == "Готово</b>&#33;"
    assert raw_of("x&") == "x&amp;"
    # An unknown entity stays literal text, one character each.
    assert raw_of("&b") == "&b"
    assert raw_of("<b>") == "&lt;b&gt;"
    plain = unit_from_text("ab", "plain")
    assert plain.raw_span(0, 2) == (0, 2) and plain.raw_span(1, 1) == (1, 1)


def test_strip_html_masks_code_and_decodes_entities():
    raw = "<p>Hi</p><pre>x = 1</pre> &lt;скамейка&gt; <code>&lt;скамейка&gt;</code>&#1080;&#x438;<br>end<i"
    text, offsets = strip_html(raw)
    assert text == "\nHi\n  <скамейка>  ии\nend"
    assert len(offsets) == len(text)
    assert raw[offsets[text.index("Hi")]:].startswith("Hi")
    assert raw[offsets[text.index("<")]:].startswith("&lt;скамейка")
    assert raw[offsets[text.index("с")]:].startswith("скамейка")
    assert raw[offsets[text.index("ии")]:].startswith("&#1080;")
    assert raw[offsets[text.index("end")]:].startswith("end")


def test_strip_html_decodes_once():
    # Single pass, as tg-cli: &amp;lt; is the literal text "&lt;", never "<".
    assert strip_html(r"&amp;lt; &bogus; &#xFFFFFFF;")[0] == r"&lt; &bogus; &#xFFFFFFF;"


def test_zero_padded_entity_is_decoded_safely():
    # Leading zeros count toward int()'s digit limit; they must not crash the run.
    zeros = "0" * 5000
    assert strip_html(f"&#{zeros}65;&#x{zeros}41;")[0] == "AA"
    # Out of range or surrogate code points stay as written (raw strings: "\xD8" is an escape).
    kept = rf"&#{zeros}99999999;&#xD800;&#55296;"
    assert strip_html(kept)[0] == kept


def _cli(stdin, monkeypatch, capsys):
    import io

    from text_lint.cli import main

    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["check", "--input-json"])
    out, err = capsys.readouterr()
    return code, out, err


def test_deeply_nested_input_is_an_input_error(monkeypatch, capsys, fake_engines):
    with pytest.raises(InputError):
        parse_input_json("[" * 100000)
    code, _, err = _cli("[" * 100000, monkeypatch, capsys)
    assert code == 2 and "nested" in err


@pytest.mark.parametrize("field, meta", [
    ({"name": "\ud800", "text": "Great question", "format": "plain"}, {}),
    ({"name": "body", "text": "Great question \udfff", "format": "plain"}, {}),
    ({"name": "body", "text": "Great question", "format": "plain\ud800"}, {}),
    ({"name": "body", "text": "Great question", "format": "plain"}, {"tag": "decision\udc00"}),
])
def test_lone_surrogate_is_an_input_error(monkeypatch, capsys, fake_engines, field, meta):
    raw = json.dumps({"schema": "text-lint/input@1", "fields": [field], "meta": meta})
    with pytest.raises(InputError) as exc:
        parse_input_json(raw)
    assert str(exc.value).encode("utf-8")  # raises on a surrogate: the message is printable
    code, _, err = _cli(raw, monkeypatch, capsys)
    assert code == 2 and "surrogate" in err
    assert err.encode("utf-8")


@pytest.mark.parametrize("fmt, text", [
    ("plain", "\ufeff\ufeffВ мире"),
    ("html", "&#xFEFF;<b>В</b> мире"),
])
def test_first_field_loses_leading_boms_spans_stay_in_the_field(fmt, text):
    units, _ = parse_input_json(json.dumps({"schema": "text-lint/input@1", "fields": [
        {"name": "body", "text": text, "format": fmt}, {"name": "tail", "text": "\ufeffx", "format": "plain"}]}))
    first, second = units
    assert first.text in ("В мире",) and second.text == "\ufeffx"
    s, e = first.raw_span(first.text.index("мире"), len(first.text))
    assert text[s:e] == "мире"
    assert first.position(0, space="text") == (1, text.index("В") + 1)
