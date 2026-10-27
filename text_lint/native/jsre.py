"""Pieces for writing tg-cli's JavaScript regexes (`u` flag) in Python's `re`.

JS `\\b`, `\\w` and `\\d` are ASCII even under the `u` flag, while Python's are Unicode, so
the ports spell them with these; JS `\\p{L}` / `\\p{N}` have no `re` syntax and are spelled
as Python classes; JS `$` without the `m` flag is Python's `\\Z`. Lengths are compared in
UTF-16 code units, as JS `.length` counts them.
"""

WORD = "[A-Za-z0-9_]"
# JS \b: a boundary between an ASCII word character and anything else.
B = rf"(?:(?<!{WORD})(?={WORD})|(?<={WORD})(?!{WORD}))"
# [\p{L}\p{N}]: a letter or number of any script (Python's \w less the underscore).
LN = r"[^\W_]"
# \p{L}: a letter of any script.
L = r"[^\W\d_]"
# (?<![\p{L}\p{N}]) and (?![\p{L}\p{N}]): tg-cli's word edges for Cyrillic words.
W = r"(?<![^\W_])"
WE = r"(?![^\W_])"


def js_len(text):
    """``text.length`` in JavaScript: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2
