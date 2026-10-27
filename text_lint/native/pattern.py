"""The `pattern` kind: a regex with DICT.json semantics, a port of tg-cli's dictionary matcher
(`features/cli/dict-gate.ts`).

Patterns are authored as JavaScript regexes with JS flags. `\\b` is read as a Unicode word
boundary (`toUnicodeBoundaryPattern`, dict-gate.ts:128), the `u` flag is forced, `g` and `y`
are dropped (every occurrence is reported anyway), `i`, `m` and `s` keep their meaning; any
other flag fails the load, as `new RegExp` throws on it. A control character in a pattern is
refused: JSON "\\b" is a backspace, and the rule would silently match nothing. Patterns are
translated to Python's `re` with JavaScript classes, anchors and Unicode 17 simple folds.
Unsupported syntax (property escapes, named groups, backreferences, inline modifiers and
quantified expressions that may match empty) fails the load rather than matching differently.

The rule reads the unit's normalized text: HTML stripped (`<pre>`/`<code>` contents dropped,
tags removed, entities decoded), Markdown with code and URLs masked, plain text as written.
`only_if_script: cyrillic` runs the rule only when the raw text of the gating units has a
Cyrillic letter (a `"gate": false` unit is judged on its own raw text): Cyrillic quoted inside
`<code>` is still Cyrillic the reader sees, as tg-cli judges it.
"""

import re
from functools import lru_cache

from ..text import mask, protected_spans
from ..unicode_scripts import CYRILLIC

# The package defines ``finding`` before it imports its kind modules.
from . import finding
from ._js_pattern import UNICODE_BOUNDARY, translate

_UNICODE_BOUNDARY = UNICODE_BOUNDARY
_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}
# JS flags with no effect on what matches: g and y (state), u (forced), d (match indices).
_NO_OP_FLAGS = set("gyud")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# \p{sc=Cyrillic}, which tg-cli tests, as one character class: a search runs in C.
_CYRILLIC = re.compile("[" + "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in zip(*CYRILLIC)) + "]")


def unicode_boundary_pattern(pattern):
    """``pattern`` with every `\\b` outside a character class rewritten to a Unicode word
    boundary; `[\\b]` (backspace), `\\B` and an escaped backslash stay as written."""
    out = []
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            following = pattern[i + 1:i + 2]
            out.append(_UNICODE_BOUNDARY if following == "b" and not in_class else ch + following)
            i += 2
            continue
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        out.append(ch)
        i += 1
    return "".join(out)


@lru_cache(maxsize=1024)
def compile_pattern(pattern, flags):
    """The compiled rule; ValueError says why it cannot be one."""
    control = _CONTROL.search(pattern)
    if control:
        cp = ord(control.group())
        hint = ' (in JSON write "\\\\b" for a regex word boundary, "\\b" is a backspace)' if cp == 8 else ""
        raise ValueError(f"pattern contains a control character U+{cp:04X}{hint}")
    value = 0
    for ch in flags:
        if ch in _FLAGS:
            value |= _FLAGS[ch]
        elif ch not in _NO_OP_FLAGS:
            raise ValueError(f"unknown flag {ch!r} (flags: i, m, s; g, y, u and d are accepted and change nothing)")
    try:
        return re.compile(translate(pattern, flags), value)
    except (re.error, OverflowError) as exc:
        raise ValueError(f"invalid regex ({exc})") from None


def check(params):
    compile_pattern(params["pattern"], params.get("flags", ""))


@lru_cache(maxsize=64)
def _markdown(text):
    spans = protected_spans(text)
    return mask(text, spans), spans


def _normalized(unit):
    """(text the rule reads, spans no match may touch): Markdown code and URLs are blanked so
    they cannot match, and a match that reaches across a blanked span is dropped too."""
    return _markdown(unit.text) if unit.format == "markdown" else (unit.text, [])


def run(rule, unit, ctx):
    if rule.params.get("only_if_script") == "cyrillic":
        scope = ctx.gating_text if unit.gate else unit.raw
        if not _CYRILLIC.search(scope):
            return []
    text, protected = _normalized(unit)
    regex = compile_pattern(rule.params["pattern"], rule.params.get("flags", ""))
    # An empty match is never a banned word.
    return [finding(rule, unit, m.start(), m.end(), space="text", match=unit.text[m.start():m.end()])
            for m in _matches(regex, text)
            if m.end() > m.start() and not any(s < m.end() and m.start() < e for s, e in protected)]


def _matches(regex, text):
    """JS matchAll advances one codepoint after an empty /u match. Python finditer may
    instead return a nonempty alternative at the same position, which JS never reports."""
    pos = 0
    while pos <= len(text):
        match = regex.search(text, pos)
        if match is None:
            return
        yield match
        pos = match.end() if match.end() > match.start() else match.end() + 1
