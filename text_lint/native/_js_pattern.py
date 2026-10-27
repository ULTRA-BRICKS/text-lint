r"""Translate the supported JS /u dictionary regex subset to Python re.

Translate character atoms before compiling: re.ASCII would break the source's Unicode
boundary rewrite, while re.IGNORECASE has different folds (notably dotted/dotless I).
The frozen Bun tables keep both boundaries and case folding independent of Python's UCD.
Backreferences, named groups, property escapes, inline modifiers and quantification of
expressions that may match empty are unsupported; reject them instead of accepting a
Python meaning. No JS runtime is needed at run time.
"""

import re

from ..unicode_categories import LETTER_NUMBER, SIMPLE_FOLDS

_FOLDS = {cp: group for group in SIMPLE_FOLDS for cp in group}
_LINE = r"[\n\r\u2028\u2029]"
_ANY = r"[\s\S]"
_SPACE = r"\x09-\x0d\x20\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def _escape(cp):
    return rf"\U{cp:08x}"


def _class(ranges, *, fold=False):
    parts = [_escape(a) if a == b else _escape(a) + '-' + _escape(b) for a, b in ranges]
    if fold:
        extra = {q for cp, group in _FOLDS.items() if any(a <= cp <= b for a, b in ranges)
                 for q in group if not any(a <= q <= b for a, b in ranges)}
        parts.extend(_escape(cp) for cp in sorted(extra))
    return '[' + ''.join(parts) + ']' if parts else '(?!)'


def _boundary(fold=False):
    word = _class([*zip(*LETTER_NUMBER), (ord('_'), ord('_'))], fold=fold)
    return rf"(?:(?<!{word})(?={word})|(?<={word})(?!{word}))"


UNICODE_BOUNDARY = _boundary()
_FOLDED_BOUNDARY = _boundary(fold=True)


class _Expression:
    """Conservative empty-match analysis of one source group, without executing a regex.

    Assertions may succeed without consuming text. A sequence may be empty when all its
    atoms may be empty; an alternative may be empty when any branch may be empty.
    """

    def __init__(self):
        self.atoms = []
        self.empty_branch = False

    def may_be_empty(self):
        return self.empty_branch or all(self.atoms)

    def alternative(self):
        self.empty_branch |= all(self.atoms)
        self.atoms = []


class _Translator:
    def __init__(self, pattern, flags):
        self.pattern = pattern
        self.pos = 0
        self.fold = 'i' in flags
        self.multiline = 'm' in flags
        self.dotall = 's' in flags
        self.groups = []
        self.expressions = [_Expression()]
        self.word = '[A-Za-z0-9_' + ('\u017f\u212a' if self.fold else '') + ']'

    def error(self, detail):
        raise ValueError(f"invalid regex or unsupported JS syntax at offset {self.pos}: {detail}")

    def reject_assertion_quantifier(self):
        if self.pattern[self.pos:self.pos + 1] in ('*', '+', '?', '{'):
            self.error('quantified assertion')

    def literal(self, cp):
        if 0xd800 <= cp <= 0xdfff:
            self.error('isolated UTF-16 surrogate')
        group = _FOLDS.get(cp, (cp,)) if self.fold else (cp,)
        return _class([(q, q) for q in group]) if len(group) > 1 else _escape(cp)

    def escape(self, in_class=False):
        """Return (single codepoint or None, regex atom); consume one escape."""
        self.pos += 1  # backslash
        if self.pos == len(self.pattern):
            self.error('trailing backslash')
        ch = self.pattern[self.pos]
        self.pos += 1
        if ch in 'wWdDsS':
            atom = {'w': self.word, 'd': '[0-9]', 's': '[' + _SPACE + ']'}[ch.lower()]
            if ch.isupper():
                atom = '[^' + atom[1:]
            return None, atom
        if ch == 'b' and not in_class:
            return None, _FOLDED_BOUNDARY if self.fold else UNICODE_BOUNDARY
        if ch == 'B' and not in_class:
            self.reject_assertion_quantifier()
            w = self.word
            return None, rf'(?:(?<={w})(?={w})|(?<!{w})(?!{w}))'
        if ch in 'fnrtv' or (ch == 'b' and in_class):
            cp = {'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11, 'b': 8}[ch]
        elif ch in 'xu':
            count = 2 if ch == 'x' else 4
            braced = ch == 'u' and self.pattern[self.pos:self.pos + 1] == '{'
            if braced:
                end = self.pattern.find('}', self.pos + 1)
                digits = self.pattern[self.pos + 1:end] if end >= 0 else ''
                self.pos = end + 1 if end >= 0 else self.pos
            else:
                digits = self.pattern[self.pos:self.pos + count]
                self.pos += count
                if len(digits) != count:
                    self.error('incomplete character escape')
            if not re.fullmatch('[0-9a-fA-F]+', digits):
                self.error('invalid character escape')
            cp = int(digits, 16)
            if cp > 0x10ffff:
                self.error('character escape above U+10FFFF')
            # JS /u combines two fixed-width UTF-16 escapes, never a braced surrogate.
            tail = self.pattern[self.pos:self.pos + 6]
            if (not braced and 0xd800 <= cp <= 0xdbff
                    and re.fullmatch(r'\\u[dD][c-fC-F][0-9a-fA-F]{2}', tail)):
                cp = 0x10000 + (cp - 0xd800) * 0x400 + int(tail[2:], 16) - 0xdc00  # noqa: FURB166 - prefix is JS escape/entity syntax, not 0x
                self.pos += 6
        elif ch == 'c':
            letter = self.pattern[self.pos:self.pos + 1]
            if not re.fullmatch('[a-zA-Z]', letter):
                self.error('invalid control escape')
            self.pos += 1
            cp = ord(letter.upper()) % 32
        elif ch == '0' and not self.pattern[self.pos:self.pos + 1].isdigit():
            cp = 0
        elif ch in r'^$\.*+?()[]{}/' or (ch == '-' and in_class):
            cp = ord(ch)
        else:
            self.error(f'escape \\{ch} (backreferences and property escapes are unsupported)')
        return cp, self.literal(cp)

    def class_item(self):
        if self.pattern[self.pos] == '\\':
            return self.escape(in_class=True)
        cp = ord(self.pattern[self.pos])
        self.pos += 1
        return cp, self.literal(cp)

    def character_class(self):
        self.pos += 1
        negative = self.pattern[self.pos:self.pos + 1] == '^'
        self.pos += negative
        atoms = []
        ranges = []
        while self.pos < len(self.pattern) and self.pattern[self.pos] != ']':
            cp, atom = self.class_item()
            if (self.pattern[self.pos:self.pos + 1] == '-'
                    and self.pattern[self.pos + 1:self.pos + 2] not in ('', ']')):
                self.pos += 1
                end, _ = self.class_item()
                if cp is None or end is None or cp > end:
                    self.error('invalid character-class range')
                ranges.append((cp, end))
            elif cp is None:
                atoms.append(atom)
            else:
                ranges.append((cp, cp))
        if self.pos == len(self.pattern):
            self.error('unterminated character class')
        self.pos += 1
        if ranges:
            atoms.append(_class(ranges, fold=self.fold))
        union = '(?:' + '|'.join(atoms) + ')' if atoms else '(?!)'
        return '(?!' + union + ')' + _ANY if negative else union

    def quantifier(self):
        """Reject nullable repeated bodies before Python can choose a different match.

        JS rejects zero-progress optional iterations, while Python can accept them.
        Keep simple a*, a? and lazy suffixes: their repeated atom still consumes text.
        """
        ch = self.pattern[self.pos]
        if ch == '{':
            match = re.match(r'\{[0-9]+(?:,[0-9]*)?\}', self.pattern[self.pos:])
            if match is None:
                self.error('invalid repetition')
            token = match.group()
            permits_zero = not token[1:-1].split(',')[0].strip('0')
        else:
            token = ch
            permits_zero = ch in '*?'
        atoms = self.expressions[-1].atoms
        if not atoms:
            self.error('nothing to repeat')
        if atoms[-1]:
            self.error('quantified expression may match empty')
        atoms[-1] = permits_zero
        self.pos += len(token)
        if self.pattern[self.pos:self.pos + 1] == '+':
            self.error('possessive quantifier')
        if self.pattern[self.pos:self.pos + 1] == '?':
            token += '?'
            self.pos += 1
        return token

    def translate(self):
        out = []
        while self.pos < len(self.pattern):
            ch = self.pattern[self.pos]
            if ch == '\\':
                assertion = self.pattern[self.pos + 1:self.pos + 2] in ('b', 'B')
                out.append(self.escape()[1])
                self.expressions[-1].atoms.append(assertion)
                continue
            if ch == '[':
                # Group negated classes so a following quantifier repeats the assertion too.
                out.append('(?:' + self.character_class() + ')')
                self.expressions[-1].atoms.append(False)
                continue
            if ch == '(' and self.pattern[self.pos:self.pos + 2] == '(?':
                prefix = next((p for p in ('(?:', '(?=', '(?!', '(?<=', '(?<!')
                               if self.pattern.startswith(p, self.pos)), None)
                if prefix is None:
                    self.error('group extension (named groups and inline modifiers are unsupported)')
                self.groups.append(prefix != '(?:')
                self.expressions.append(_Expression())
                out.append(prefix)
                self.pos += len(prefix)
                continue
            if ch in '*+?{':
                out.append(self.quantifier())
                continue
            self.pos += 1
            if ch == '(':
                self.groups.append(False)
                self.expressions.append(_Expression())
            elif ch == ')' and self.groups:
                assertion = self.groups.pop()
                expression = self.expressions.pop()
                self.expressions[-1].atoms.append(assertion or expression.may_be_empty())
                if assertion:
                    self.reject_assertion_quantifier()
            elif ch == '|':
                self.expressions[-1].alternative()
            elif ch != ')':
                self.expressions[-1].atoms.append(ch in '^$')
            if ch in '^$':
                self.reject_assertion_quantifier()
            if ch == '.':
                out.append(_ANY if self.dotall else r'[^\n\r\u2028\u2029]')
            elif ch == '^':
                out.append(rf'(?:\A|(?<={_LINE}))' if self.multiline else r'\A')
            elif ch == '$':
                out.append(rf'(?=\Z|{_LINE})' if self.multiline else r'\Z')
            elif ch in '*+?()|':
                out.append(ch)
            elif ch in ']}':
                self.error(f'unescaped {ch}')
            else:
                out.append(self.literal(ord(ch)))
        # Retain the declared flags for introspection, but all their matching behavior is
        # implemented above. Scoped disabling avoids Python's different Unicode folds.
        return '(?-ims:' + ''.join(out) + ')'


def translate(pattern, flags):
    return _Translator(pattern, flags).translate()
