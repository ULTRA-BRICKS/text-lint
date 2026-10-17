"""Text plumbing shared by the engines: protected regions, paragraph language, offsets.

Every transformation here keeps the text's length and its newlines, so an offset, line or
column an engine reports on a masked copy points at the same character of the input.
"""

import bisect
import re

# A fence may open inside list items and block quotes ("- ```", "> ```").
# Up to three spaces of indent at each level; four make it an indented code block line.
_FENCE = re.compile(r"^ {0,3}(?:(?:(?:[-*+]|\d+[.)]) {1,4}|> ?) {0,3})*(`{3,}|~{3,})")
# A code span may wrap lines but, as in CommonMark, never crosses a blank line.
_INLINE_CODE = re.compile(r"(`+)(?!`)((?:[^\n]|\n(?![ \t]*\n))+?)(?<!`)\1(?!`)")
# Bare URLs, <autolinks> and www. hosts. Trailing sentence punctuation is not part of a URL.
# Balanced "(...)" segments stay inside the URL (Wikipedia-style paths).
_URL = re.compile(r"<https?://[^\s>]+>|\b(?:https?://|www\.)(?:[^\s<>()\[\]`\"']|\([^\s<>()`\"']*\))+")
_URL_TAIL = ".,;:!?'\"»"
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_LATIN = re.compile(r"[A-Za-z]")


def protected_spans(text):
    """[start, end) spans of fenced code blocks, inline code and URLs: never flagged."""
    spans = []
    lines = text.splitlines(keepends=True)
    pos = 0
    fence = None  # (marker char, length, start offset, containers) while inside a fenced block
    prev_end = 0  # end of the previous line, newline excluded
    for line in lines:
        m = _FENCE.match(line)
        if fence is not None and not _in_container(line, fence[3]):
            # A fence opened in a list item or block quote ends with that container.
            spans.append((fence[2], prev_end))
            fence = None
        if m and m.group(1)[0] == "`" and "`" in line[m.end():]:
            m = None  # a backtick fence's info string cannot hold a backtick: this is prose
        if fence is None and m:
            container = _containers(line[:m.start(1)])
            fence = (m.group(1)[0], len(m.group(1)), pos, container)
        elif fence is not None and m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not line[m.end():].strip():
            spans.append((fence[2], pos + len(line.rstrip("\r\n"))))
            fence = None
        prev_end = pos + len(line.rstrip("\r\n"))
        pos += len(line)
    if fence is not None:
        spans.append((fence[2], len(text)))

    fenced = list(spans)
    for m in _INLINE_CODE.finditer(text):
        if not _overlaps(fenced, m.start(), m.end()):
            spans.append((m.start(), m.end()))
    for m in _URL.finditer(text):
        end = m.end()
        while end > m.start() and text[end - 1] in _URL_TAIL:
            end -= 1
        if not _overlaps(spans, m.start(), end):
            spans.append((m.start(), end))
    return sorted(spans)


_QUOTE = re.compile(r"[ \t]*>[ \t]?")
_LIST_MARKER = re.compile(r"[ \t]*(?:[-*+]|\d+[.)])[ \t]+")


def _containers(prefix):
    """The nested block quotes and list items a fence's ``prefix`` opens it in, outermost
    first: ("quote", 0) or ("list", content indent within what the outer levels leave)."""
    levels = []
    while prefix.strip():
        m = _QUOTE.match(prefix)
        if m:
            levels.append(("quote", 0))
        else:
            m = _LIST_MARKER.match(prefix)
            if not m:
                break
            levels.append(("list", m.end()))
        prefix = prefix[m.end():]
    return levels


def _in_container(line, levels):
    """Whether ``line`` still belongs to every container in ``levels``."""
    if not line.strip():
        return True
    for kind, indent in levels:
        if kind == "quote":
            m = _QUOTE.match(line)
            if not m:
                return False
            line = line[m.end():]
            if not line.strip():
                return True
        else:
            if len(line) - len(line.lstrip(" \t")) < indent:
                return False
            line = line[indent:]
    return True


def _overlaps(spans, start, end):
    return any(s < end and start < e for s, e in spans)


def mask(text, spans):
    """``text`` with every span blanked to spaces; newlines stay so lines and columns hold."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            if chars[i] not in "\r\n":
                chars[i] = " "
    return "".join(chars)


def paragraphs(text):
    """[start, end) spans of blank-line separated blocks, trailing newline excluded."""
    spans = []
    start = None
    pos = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            if start is None:
                start = pos
            end = pos + len(line.rstrip("\r\n"))
        elif start is not None:
            spans.append((start, end))
            start = None
        pos += len(line)
    if start is not None:
        spans.append((start, end))
    return spans


def is_russian(segment):
    """Whether Cyrillic letters are at least half of the segment's Cyrillic + Latin letters."""
    cyr = len(_CYRILLIC.findall(segment))
    lat = len(_LATIN.findall(segment))
    return cyr > 0 and cyr >= lat


def has_cyrillic(segment):
    return _CYRILLIC.search(segment) is not None


class LineIndex:
    """Offset <-> (line, col) with 1-based lines and columns counted in characters."""

    def __init__(self, text):
        self.text = text
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def offset(self, line, col):
        """IndexError for a position outside the text (an engine reporting nonsense)."""
        if not (isinstance(line, int) and isinstance(col, int)) or not 1 <= line <= len(self.starts) or col < 1:
            raise IndexError(f"no line {line!r}, column {col!r} in the text")
        offset = self.starts[line - 1] + col - 1
        line_end = self.starts[line] - 1 if line < len(self.starts) else len(self.text)
        if offset > line_end:
            raise IndexError(f"line {line}, column {col} is past the end of that line")
        return offset

    def line_col(self, offset):
        i = bisect.bisect_right(self.starts, offset) - 1
        return i + 1, offset - self.starts[i] + 1

    def line_text(self, line):
        start = self.starts[line - 1]
        end = self.starts[line] - 1 if line < len(self.starts) else len(self.text)
        return self.text[start:end]
