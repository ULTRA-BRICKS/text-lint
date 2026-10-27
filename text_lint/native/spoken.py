"""Markup and emoji that should not be spoken literally."""

import re

from .as_user import EMOJI, FORMATTING, matches

MARKUP = re.compile(f"(?:{FORMATTING.pattern})|(?:{EMOJI.pattern})|https?://[^\\s<>]+")


def run(rule, unit, ctx):
    return matches(rule, unit, MARKUP)
