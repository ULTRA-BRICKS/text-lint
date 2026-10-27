"""Text plumbing shared by the engines: protected regions, paragraph language, offsets.

Masking keeps the text's length and its newlines, so an offset, line or column an engine
reports on a masked copy points at the same character of the input. HTML stripping changes
the length and returns an offset map instead.
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
    spans += [(s, e) for s, e in url_spans(text) if not _overlaps(spans, s, e)]
    return sorted(spans)


def url_spans(text):
    """[start, end) spans of the URLs in ``text``, trailing sentence punctuation excluded."""
    spans = []
    for m in _URL.finditer(text):
        end = m.end()
        while end > m.start() and text[end - 1] in _URL_TAIL:
            end -= 1
        spans.append((m.start(), end))
    return spans


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


# HTML normalization, a port of tg-cli's stripForDictionary (features/cli/dict-gate.ts:289):
# <pre> and <code> elements become one space, block tags a newline so the words on either
# side stay apart, other tags vanish, then entities decode in a single pass.
_PRE = re.compile(r"<pre\b[^>]*>[\s\S]*?</pre\s*>", re.IGNORECASE)
_CODE = re.compile(r"<code\b[^>]*>[\s\S]*?</code\s*>", re.IGNORECASE)
_BLOCK_TAG = re.compile(
    r"</?(?:br|p|div|li|ul|ol|tr|td|th|table|thead|tbody|caption|h[1-6]|hr|blockquote|details|"
    r"summary|section|article)\b[^>]*>", re.IGNORECASE)
# render/html.ts stripHtmlTags: whole tags, and an unclosed tag at the very end of the text.
_TAG = re.compile(r"<[^>]*>|<[/!A-Za-z][^>]*\Z")
_ENTITY = re.compile(r"&(#x[0-9a-f]+|#[0-9]+|[a-z]+);", re.IGNORECASE)
_NAMED_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}


def _decode_entity(m):
    body = m.group(1)
    if body[0] == "#":
        digits, base = (body[2:], 16) if body[1] in "xX" else (body[1:], 10)
        # Leading zeros count toward int()'s digit limit, so they go first; anything longer
        # than a code point is left as written.
        digits = digits.lstrip("0") or "0"
        cp = int(digits, base) if len(digits) <= 7 else -1
        # A lone surrogate could not be written to an engine's stdin; it stays as written too.
        return chr(cp) if 0 <= cp <= 0x10FFFF and not 0xD800 <= cp <= 0xDFFF else m.group()
    return _NAMED_ENTITIES.get(body.lower(), m.group())


def _replace(text, offsets, pattern, repl):
    """``pattern`` replaced by ``repl(match)`` in ``text``; every output character keeps the
    input offset of what it came from (a replacement maps to the start of its match)."""
    out, out_offsets, pos = [], [], 0
    for m in pattern.finditer(text):
        out.append(text[pos:m.start()])
        out_offsets.extend(offsets[pos:m.start()])
        new = repl(m)
        out.append(new)
        # Text left as written (an unknown entity) keeps its own offsets.
        out_offsets.extend(offsets[m.start():m.end()] if new == m.group() else [offsets[m.start()]] * len(new))
        pos = m.end()
    out.append(text[pos:])
    out_offsets.extend(offsets[pos:])
    return "".join(out), out_offsets


def strip_html(text):
    """(what a Telegram reader sees of the HTML ``text``, the offset in ``text`` of each of its
    characters)."""
    offsets = list(range(len(text)))
    for pattern, repl in ((_PRE, lambda m: " "), (_CODE, lambda m: " "), (_BLOCK_TAG, lambda m: "\n"),
                          (_TAG, lambda m: ""), (_ENTITY, _decode_entity)):
        text, offsets = _replace(text, offsets, pattern, repl)
    return text, offsets


def html_source_end(raw, offset, char, limit):
    """End in ``raw`` of what became ``char``, a character of ``strip_html(raw)`` whose source
    starts at ``offset``; ``limit`` is where the next output character's source starts. A
    decoded entity (`&mdash;`) or a replaced element covers its whole source; a literal
    character covers one."""
    m = _ENTITY.match(raw, offset)
    if m and m.end() <= limit and _decode_entity(m) == char:
        return m.end()
    if raw[offset:offset + 1] != char:
        for pattern in (_PRE, _CODE, _BLOCK_TAG):
            m = pattern.match(raw, offset)
            if m and m.end() <= limit:
                return m.end()
    return offset + 1
