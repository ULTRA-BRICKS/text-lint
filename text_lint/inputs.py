"""What a check reads: units of text (one per input@1 field, or one for a file or stdin) and
the message meta data. input@1 is text_lint/schema/input-v1.json; it is validated here by
hand, so a run needs no jsonschema."""

import json
import re
from dataclasses import dataclass, replace
from functools import cached_property
from typing import Literal

from .text import LineIndex, html_source_end, strip_html

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

    ``raw`` is the unchanged source. ``semantic`` retains markup for native rules, with
    CRLF and lone CR normalized to LF. ``text`` is the same semantic input with HTML
    stripped for pattern and external engines. API strings and the first input@1 field
    lose all leading decoded BOMs; embedded BOMs and later fields retain them.
    ``norm_to_raw`` and ``semantic_to_raw`` map characters to original source offsets;
    None denotes identity. ``raw_map`` optionally maps source offsets to extracted-file
    positions. Every report span remains in ``raw``, in Python codepoints.
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

    semantic_text: str | None = None
    semantic_to_raw: list[int] | None = None

    @property
    def semantic(self):
        """Normalized source with markup retained; identity when no changes are needed."""
        return self.raw if self.semantic_text is None else self.semantic_text

    def semantic_offset(self, offset):
        if self.semantic_to_raw is None:
            return offset
        return self.semantic_to_raw[offset] if offset < len(self.semantic_to_raw) else len(self.raw)

    def semantic_span(self, start, end):
        return self._source_span(self.semantic, self.semantic_to_raw, start, end)

    def raw_offset(self, offset):
        """The ``raw`` offset of ``text[offset]``; the end of ``text`` maps to the end of ``raw``."""
        if self.norm_to_raw is None:
            return offset
        return self.norm_to_raw[offset] if offset < len(self.norm_to_raw) else len(self.raw)

    def raw_span(self, start, end):
        """[start, end) in ``raw`` of ``text[start:end]``: an HTML match that ends on a decoded
        entity covers the whole entity, never a removed tag after it."""
        return self._source_span(self.text, self.norm_to_raw, start, end, html=self.format == "html")

    def _source_span(self, text, mapping, start, end, *, html=False):
        def offset(i):
            return i if mapping is None else mapping[i] if i < len(mapping) else len(self.raw)
        raw_start = offset(start)
        if mapping is None or end <= start:
            return raw_start, max(raw_start, offset(end))
        last = mapping[end - 1]
        if text[end - 1] == "\n" and self.raw[last:last + 2] == "\r\n":
            return raw_start, last + 2
        if html:
            return raw_start, html_source_end(self.raw, last, text[end - 1], offset(end))
        return raw_start, last + 1

    @cached_property
    def _raw_index(self):
        return LineIndex(self.raw)

    def position(self, offset, *, space):
        """1-based (line, col) in the source of ``offset`` into ``text`` (``space="text"``) or
        ``semantic`` (``space="semantic"``), or ``raw`` (``space="raw"``). The end of the text is the column after its last character."""
        if space == "text":
            offset = self.raw_offset(offset)
        elif space == "semantic":
            offset = self.semantic_offset(offset)
        elif space != "raw":
            raise ValueError(f"space must be 'text', 'semantic' or 'raw', not {space!r}")
        offset = max(0, min(offset, len(self.raw)))
        if self.raw_map is not None:
            if offset < len(self.raw_map):
                return self.raw_map[offset]
            if not self.raw_map:
                return 1 + self.line_offset, 1
            line, col = self.raw_map[-1]
            return line, col + 1
        line, col = self._raw_index.line_col(offset)
        return line + self.line_offset, col


def unit_from_text(text, fmt, *, field=None, gate=True):
    """Build a canonical unit without changing its source. Native rules retain markup;
    pattern rules and external engines read HTML stripped. Markdown protection is applied
    by the check, so masking cannot change a block's interpretation by an engine."""
    if fmt not in FORMATS:
        raise InputError(f"format must be one of {', '.join(FORMATS)}, not {fmt!r}")
    semantic, offsets = _newlines(text)
    normalized, mapping = strip_html(semantic) if fmt == "html" else (semantic, None)
    if offsets is not None:
        mapping = offsets if mapping is None else [offsets[j] for j in mapping]
    return Unit(text=normalized, raw=text, format=fmt, field=field, path=None, gate=gate,
                norm_to_raw=mapping, semantic_text=semantic if offsets is not None else None,
                semantic_to_raw=offsets)


def _newlines(raw):
    if "\r" not in raw:
        return raw, None
    chars, offsets = [], []
    i = 0
    while i < len(raw):
        offsets.append(i)
        ch = raw[i]
        chars.append("\n" if ch == "\r" else ch)
        i += 2 if ch == "\r" and raw[i:i + 2] == "\r\n" else 1
    return "".join(chars), offsets


def unit_from_api_text(raw, fmt):
    """Use the first input@1 field's normalization and unchanged-source coordinate contract.

    Without CR or leading decoded BOMs, return the canonical unit directly; plain and
    Markdown keep the caller's string object and no identity offset list is allocated.
    """
    return _without_bom(unit_from_text(raw, fmt))


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
    """Drop all leading decoded BOMs from both semantic views, preserving source offsets.

    HTML entities contribute their complete source range; surrounding tags remain intact.
    aiw-ru's utf-8-sig reader must never consume a second unaccounted leading BOM.
    """
    count = len(unit.text) - len(unit.text.lstrip("\ufeff"))
    if not count:
        return unit
    ranges = [unit.raw_span(i, i + 1) for i in range(count)]
    chars, semantic_offsets = [], []
    r = 0
    for i, ch in enumerate(unit.semantic):
        offset = unit.semantic_offset(i)
        while r < len(ranges) and offset >= ranges[r][1]:
            r += 1
        if r < len(ranges) and ranges[r][0] <= offset < ranges[r][1]:
            continue
        chars.append(ch)
        semantic_offsets.append(offset)
    offsets = unit.norm_to_raw if unit.norm_to_raw is not None else range(len(unit.raw))
    return replace(unit, text=unit.text[count:], norm_to_raw=list(offsets[count:]),
                   semantic_text="".join(chars), semantic_to_raw=semantic_offsets)


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
