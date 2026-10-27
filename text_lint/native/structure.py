"""The `long-unstructured` kind (Structure.LongUnstructured): a long plain message with no
visible structure. A port of tg-cli's `shouldWarnLongUnstructuredPlainMessage`
(features/cli/message-structure.ts:169), an advisory (`suggestion`) that never blocks.

It reads the raw message unit and is skipped when the send already carries a readable
artifact or mode: attachments, a `--table` send, or an explicit `--format` other than plain
(`meta.send_format`; null is tg-cli's default, plain).
"""

import math
import re
import unicodedata

from . import finding
from .jsre import js_len

_SENTENCE_LIKE_LABEL_STARTS = {"After", "Although", "And", "As", "Because", "Before", "But", "If", "Once", "Since",
                               "So", "Then", "When", "While"}
_COMMON_HEADING_STARTS = {"Action", "Build", "Context", "Decision", "Details", "Findings", "Impact", "Implementation",
                          "Improvements", "Next", "Notes", "Plan", "Questions", "Recommendation", "Results", "Risks",
                          "Status", "Summary", "Testing"}
_LABEL_RE = re.compile(r"(?:[^\W\d_]|[0-9])(?:[^\W_]|[ /,()._-]){0,47}")
_STYLED_RE = re.compile(r"^<(b|strong|i|em|u|ins)\b[^>]*>\s*([^<]{1,80}?)\s*</\1>:?(?:\s|\Z)", re.IGNORECASE)
_LIST_RE = re.compile(r"(?:\A|\n)[ \t]*(?:[-*+\u2022\u2023\u2043\u25E6]\s+\S|[0-9]{1,3}[.)]\s+\S|\[[ xX]\]\s+\S|>\s+\S)")
_DIVIDER_RE = re.compile(r"\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?")


def _first(word):
    return word[:1]


def _category(char):
    return unicodedata.category(char) if char else ""


def _lowercase_start(word):
    return _category(_first(word)) == "Ll"


def _title_start(word):
    first = _first(word)
    return (first != "" and first in "0123456789") or _category(first) == "Lu"


def _strip_edges(word):
    """``word`` without leading and trailing characters that are neither a letter nor an
    ASCII digit (JS `[^\\p{L}0-9]`)."""
    start, end = 0, len(word)
    while start < end and not (word[start].isalpha() or word[start] in "0123456789"):
        start += 1
    while end > start and not (word[end - 1].isalpha() or word[end - 1] in "0123456789"):
        end -= 1
    return word[start:end]


def _title_like(label):
    if not _LABEL_RE.fullmatch(label):
        return False
    words = label.split()
    if not words or len(words) > 8:
        return False
    first = _strip_edges(words[0])
    if _lowercase_start(first):
        return False
    if re.fullmatch(r"[A-Za-z]+", first):
        if first in _SENTENCE_LIKE_LABEL_STARTS:
            return False
        if first in _COMMON_HEADING_STARTS:
            return True
    half = math.ceil(len(words) / 2)
    if re.search(r"[^\x00-\x7F]", label):
        if len(words) == 1:
            return True
        lowercase = sum(1 for word in words[1:] if _lowercase_start(word))
        return lowercase == 0 and sum(1 for word in words if _title_start(word)) >= half
    if len(words) == 1:
        return False
    lowercase = sum(1 for word in words[1:] if re.match(r"[a-z]", word))
    return lowercase <= 1 or sum(1 for word in words if re.match(r"[A-Z0-9]", word)) >= half


def _has_heading(text):
    for line in re.split(r"\r?\n", text):
        trimmed = line.strip()
        if re.match(r"#{1,6}\s+\S", trimmed):
            return True
        styled = _STYLED_RE.match(trimmed)
        if styled and styled.group(2) and _title_like(re.sub(r":\Z", "", styled.group(2).strip())):
            return True
        if _title_like(trimmed[:-1] if trimmed.endswith(":") else ""):
            return True
    return False


def _cells(line, delimiter):
    return sum(1 for cell in line.split(delimiter) if cell.strip())


def _consistent(counts):
    return len(counts) >= 2 and counts[0] >= 2 and all(count == counts[0] for count in counts)


def _has_table(text):
    lines = [line.strip() for line in re.split(r"\r?\n", text)]
    lines = [line for line in lines if line]
    box_rows = [line for line in lines if re.search("[┌┬┐├┼┤└┴┘│]", line)]
    if len(box_rows) >= 2 and any(re.search("[┌┬┐├┼┤└┴┘]", line) for line in box_rows):
        return True
    if _consistent([_cells(line, "\t") for line in lines if "\t" in line]):
        return True
    pipe_rows = [line for line in lines if _DIVIDER_RE.fullmatch(line) or line.startswith("|")
                 or line.endswith("|") or re.search(r"\s\|\s", line)]
    has_divider = any(_DIVIDER_RE.fullmatch(line) for line in pipe_rows)
    counts = [_cells(line, "|") for line in pipe_rows if not _DIVIDER_RE.fullmatch(line)]
    return any(count >= 2 for count in counts) if has_divider else _consistent(counts)


def has_visible_structure(text):
    return bool(re.search(r"\r?\n[ \t]*\r?\n", text) or _has_heading(text) or _LIST_RE.search(text)
                or _has_table(text) or re.search(r"(?:\A|\n)```", text) or re.search(r"<pre\b", text, re.IGNORECASE)
                or re.search(r"<blockquote\b", text, re.IGNORECASE))


def run(rule, unit, ctx):
    meta = ctx.meta
    if unit is not ctx.message or meta.attachments > 0 or meta.table or meta.send_format not in (None, "plain"):
        return []
    text = unit.raw
    if js_len(re.sub(r"\s+", " ", text).strip()) < rule.params["min_chars"] or has_visible_structure(text):
        return []
    return [finding(rule, unit, 0, 0, space="raw", match="")]
