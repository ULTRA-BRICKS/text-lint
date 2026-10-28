"""The script kinds: ports of tg-cli's garbled-token guards. Both read the raw field text, as
tg-cli reads the raw caption: a garble is a defect inside code too, and HTML tags are ASCII
and cannot trigger them. Every hit is reported, not only the first.

- `stray-cjk` (`Script.StrayCjk`), `findStrayCjk` (features/cli/cjk-guard.ts:98): a run of
  exactly one CJK codepoint (Han, Hiragana, Katakana, Hangul) followed straight away by a
  Latin or Cyrillic letter: the ideograph splits or prefixes a word (`ка日eat`, `注ляет`). A
  longer run is a bilingual word, a lone CJK ending a token is a suffix (`iOS版`), and a field
  whose CJK share reaches `dominant_share` with at least `dominant_min` CJK codepoints is CJK
  text and is not scanned.
- `mixed-script` (`Script.MixedScript`), `findMixedScriptTokens` (mixed-script-guard.ts:142):
  a run of letters whose script switches between Cyrillic and a foreign alphabet at least
  twice (`почčesна`, `mіr`). Marks, invisible format characters and modifier letters ride in
  the run without voting; CJK letters neither vote nor switch (they are stray-cjk's). One
  plain-ASCII English island inside Cyrillic (`заpushил`) passes unless it is a single letter
  or made only of Cyrillic lookalikes (`пеpeход`).
"""

from ..unicode_categories import BASE_LETTER, LETTERISH
from ..unicode_scripts import (
    CYRILLIC,
    HAN,
    HANGUL,
    HIRAGANA,
    KATAKANA,
    LATIN,
    in_script,
)

# The package defines ``finding`` before it imports its kind modules.
from . import finding

# ASCII letters that render like a Russian Cyrillic letter (a→а, B→В, p→р, …).
_CYRILLIC_LOOKALIKES = frozenset("aceopxyABCEHKMOPTXY")


def is_cjk(ch):
    """Han, Hiragana, Katakana or Hangul: the one definition both script kinds use."""
    return any(in_script(ch, script) for script in (HAN, HIRAGANA, KATAKANA, HANGUL))


def _is_latin_cyrillic(ch):
    return in_script(ch, LATIN) or in_script(ch, CYRILLIC)


def stray_cjk(rule, unit, ctx):
    text = unit.semantic
    cjk = sum(1 for ch in text if is_cjk(ch))
    latin_cyrillic = sum(1 for ch in text if _is_latin_cyrillic(ch))
    if cjk >= rule.params["dominant_min"] and cjk / (cjk + latin_cyrillic) >= rule.params["dominant_share"]:
        return []
    found = []
    i = 0
    while i < len(text):
        if not is_cjk(text[i]):
            i += 1
            continue
        start = i
        while i < len(text) and is_cjk(text[i]):
            i += 1
        if i - start == 1 and i < len(text) and _is_latin_cyrillic(text[i]):
            found.append(finding(rule, unit, start, i, space="semantic", match=text[start]))
    return found


def _letterish(ch):
    """Part of a letter run: a letter, a combining mark or an invisible format character."""
    return in_script(ch, LETTERISH)


def _voting_class(ch):
    """"cyrillic" or "foreign" for a base letter; None for what does not vote (non-letters,
    marks, format characters, modifier letters, CJK)."""
    if not in_script(ch, BASE_LETTER) or is_cjk(ch):
        return None
    return "cyrillic" if in_script(ch, CYRILLIC) else "foreign"


def _is_garbage(run):
    transitions = 0
    first = previous = None
    foreign = []
    for ch in run:
        cls = _voting_class(ch)
        if cls is None:
            continue
        if cls == "foreign":
            foreign.append(ch)
        if previous is not None and cls != previous:
            transitions += 1
        first = first or cls
        previous = cls
    if transitions < 2:
        return False
    one_english_island = (transitions == 2 and first == "cyrillic" and len(foreign) >= 2
                          and all(ch.isascii() and ch.isalpha() for ch in foreign)
                          and any(ch not in _CYRILLIC_LOOKALIKES for ch in foreign))
    return not one_english_island


def mixed_script(rule, unit, ctx):
    text = unit.semantic
    found = []
    i = 0
    while i < len(text):
        if not _letterish(text[i]):
            i += 1
            continue
        start = i
        while i < len(text) and _letterish(text[i]):
            i += 1
        if _is_garbage(text[start:i]):
            found.append(finding(rule, unit, start, i, space="semantic", match=text[start:i]))
    return found
