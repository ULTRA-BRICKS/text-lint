"""What a check reads: units of text (one per input@1 field, or one for a file or stdin) and
the message meta data. input@1 is text_lint/schema/input-v1.json; it is validated here by
hand, so a run needs no jsonschema."""

import json
import re
from dataclasses import dataclass, replace
from typing import Literal

from .text import html_source_end, strip_html

SCHEMA_ID = "text-lint/input@1"
FORMATS = ("plain", "markdown", "html")

Format = Literal["plain", "markdown", "html"]


class InputError(Exception):
    """The input is not a valid input@1 document; the CLI exits 2 with this message."""


@dataclass
class Meta:
    tag: str | None = None
    agent_authored: bool = False
    automated: bool = False
    # The sender's explicit format flag, independent of each field's detected format.
    send_format: Format | None = None
    attachments: int = 0
    table: bool = False


@dataclass
class Unit:
    """One piece of text checked as a whole.

    ``raw`` is the field as sent (script, spoken and message rules read it); ``text`` is the
    normalized text the pattern rules and the Vale/aiw-ru engines read: HTML stripped, else
    ``raw`` itself, less any leading BOM in the first input@1 field. ``norm_to_raw[j]`` is the
    offset in ``raw`` of ``text[j]`` (None: the same offset); ``raw_map[i]`` is the 1-based
    source (line, col) of ``raw[i]`` (None: counted in ``raw``, starting ``line_offset`` lines
    down).
    """

    text: str
    raw: str
    format: Format
    field: str | None
    path: str | None
    key: str | None = None
    gate: bool = True
    line_offset: int = 0
    raw_map: list[tuple[int, int]] | None = None
    norm_to_raw: list[int] | None = None

    def raw_offset(self, offset):
        """The ``raw`` offset of ``text[offset]``; the end of ``text`` maps to the end of ``raw``."""
        if self.norm_to_raw is None:
            return offset
        return self.norm_to_raw[offset] if offset < len(self.norm_to_raw) else len(self.raw)

    def raw_span(self, start, end):
        """[start, end) in ``raw`` of ``text[start:end]``: an HTML match that ends on a decoded
        entity covers the whole entity, never a removed tag after it."""
        raw_start = self.raw_offset(start)
        if self.norm_to_raw is None or end <= start:
            return raw_start, max(raw_start, self.raw_offset(end))
        last = self.norm_to_raw[end - 1]
        if self.format == "html":
            return raw_start, html_source_end(self.raw, last, self.text[end - 1], self.raw_offset(end))
        return raw_start, last + 1

    def position(self, offset, *, space):
        """1-based (line, col) in the source of ``offset`` into ``text`` (``space="text"``) or
        ``raw`` (``space="raw"``). The end of the text is the column after its last character."""
        if space == "text":
            offset = self.raw_offset(offset)
        elif space != "raw":
            raise ValueError(f"space must be 'text' or 'raw', not {space!r}")
        offset = max(0, min(offset, len(self.raw)))
        if self.raw_map is not None:
            if offset < len(self.raw_map):
                return self.raw_map[offset]
            if not self.raw_map:
                return 1 + self.line_offset, 1
            line, col = self.raw_map[-1]
            return line, col + 1
        line_start = self.raw.rfind("\n", 0, offset) + 1
        return self.raw.count("\n", 0, offset) + 1 + self.line_offset, offset - line_start + 1


def unit_from_text(text, fmt, *, field=None, gate=True):
    """A unit for ``text`` in format ``fmt``. Markdown reaches the engines as written (blanking
    a code span could turn a list item into a code block the engines skip); the check drops
    findings that touch its code and URLs instead."""
    if fmt not in FORMATS:
        raise InputError(f"format must be one of {', '.join(FORMATS)}, not {fmt!r}")
    if fmt == "html":
        normalized, norm_to_raw = strip_html(text)
        return Unit(text=normalized, raw=text, format=fmt, field=field, path=None, gate=gate,
                    norm_to_raw=norm_to_raw)
    return Unit(text=text, raw=text, format=fmt, field=field, path=None, gate=gate)


_FIELD_KEYS = {"name", "text", "format", "gate"}
_META_TYPES = {"tag": (str, type(None)), "agent_authored": bool, "automated": bool,
               "send_format": (str, type(None)), "attachments": int, "table": bool}


def parse_input_json(raw):
    """(units, meta) of an input@1 document; InputError says what is wrong."""
    try:
        data = json.loads(raw)
    except RecursionError:
        raise InputError("input is nested too deeply to be an input@1 document") from None
    except ValueError as exc:
        raise InputError(f"input is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise InputError("input@1 must be a JSON object")
    _no_extra(data, {"schema", "fields", "meta"}, "input")
    if data.get("schema") != SCHEMA_ID:
        raise InputError(f'"schema" must be "{SCHEMA_ID}", got {data.get("schema")!r}')
    fields = data.get("fields")
    if not isinstance(fields, list):
        raise InputError('"fields" must be a list')
    units = [_field_unit(field, i) for i, field in enumerate(fields)]
    if units:
        units[0] = _without_bom(units[0])
    return units, _meta(data.get("meta", {}))


def _without_bom(unit):
    """``unit`` with leading BOMs dropped from the text the engines read, as the file and stdin
    path drops one: aiw-ru reads its input as utf-8-sig and drops one more itself, so its
    offsets would skip it. A BOM is invisible; spans still point into the field as sent."""
    count = len(unit.text) - len(unit.text.lstrip("\ufeff"))
    if not count:
        return unit
    offsets = unit.norm_to_raw if unit.norm_to_raw is not None else list(range(len(unit.raw)))
    return replace(unit, text=unit.text[count:], norm_to_raw=offsets[count:])


_SURROGATE = re.compile(r"[\ud800-\udfff]")


def _no_surrogate(value, where):
    """A lone surrogate (valid in JSON, not in UTF-8) can be neither sent to an engine nor
    printed; the message names its place, never the value."""
    m = _SURROGATE.search(value)
    if m:
        raise InputError(f"{where} holds a lone surrogate (U+{ord(m.group()):04X}) at offset {m.start()}")


def _no_extra(obj, allowed, where):
    extra = sorted(set(obj) - allowed)
    if extra:
        raise InputError(f"{where}: unknown key(s) {', '.join(map(repr, extra))}")


def _field_unit(field, i):
    where = f"fields[{i}]"
    if not isinstance(field, dict):
        raise InputError(f"{where} must be an object")
    _no_extra(field, _FIELD_KEYS, where)
    for key in ("name", "text", "format"):
        if not isinstance(field.get(key), str):
            raise InputError(f'{where}: "{key}" must be a string')
        _no_surrogate(field[key], f'{where}: "{key}"')
    if field["format"] not in FORMATS:
        raise InputError(f'{where}: "format" must be one of {", ".join(FORMATS)}, not {field["format"]!r}')
    gate = field.get("gate", True)
    if not isinstance(gate, bool):
        raise InputError(f'{where}: "gate" must be true or false')
    return unit_from_text(field["text"], field["format"], field=field["name"], gate=gate)


def _meta(meta):
    if not isinstance(meta, dict):
        raise InputError('"meta" must be an object')
    _no_extra(meta, set(_META_TYPES), "meta")
    for key, types in _META_TYPES.items():
        # bool is an int in Python; a count must not be true/false.
        if key in meta and (not isinstance(meta[key], types) or (types is int and isinstance(meta[key], bool))):
            raise InputError(f'meta: "{key}" has the wrong type: {meta[key]!r}')
    tag = meta.get("tag")
    if isinstance(tag, str):
        _no_surrogate(tag, 'meta: "tag"')
    if meta.get("send_format") not in (None, *FORMATS):
        raise InputError(f'meta: "send_format" must be one of {", ".join(FORMATS)} or null')
    if meta.get("attachments", 0) < 0:
        raise InputError('meta: "attachments" must not be negative')
    return Meta(**meta)
