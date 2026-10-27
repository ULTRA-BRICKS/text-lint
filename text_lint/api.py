"""Stable in-process entry point for consumers of input@1 and report@1."""

import json
import os
from os import PathLike
from typing import Literal, NotRequired, TypedDict

from .check import check_units
from .engines import EngineError
from .inputs import InputError, Meta, _no_surrogate, parse_input_json, unit_from_text
from .profiles import ConfigError, load_profile


class InputField(TypedDict):
    name: str
    text: str
    format: Literal["plain", "markdown", "html"]
    gate: NotRequired[bool]


class InputMeta(TypedDict, total=False):
    tag: str | None
    agent_authored: bool
    automated: bool
    send_format: Literal["plain", "markdown", "html"] | None
    attachments: int
    table: bool


class InputDocument(TypedDict):
    schema: Literal["text-lint/input@1"]
    fields: list[InputField]
    meta: NotRequired[InputMeta]


class TextLintError(Exception):
    """Invalid input/configuration or a required engine failure (CLI exit 2)."""


def check(text: str | InputDocument, *, profile: str = "prose",
          lang: Literal["auto", "en", "ru"] = "auto",
          enforce: Literal["block", "warn"] = "block",
          laya_mode: Literal["required", "ci"] = "required",
          dict_path: str | PathLike[str] | None = None) -> dict:
    """Return report@1 without printing or exiting; raise TextLintError for exit-2 cases.

    Strings use the profile's markup and default sender metadata. Documents preserve
    input@1 fields and metadata. ``ci`` requires CI=true and never starts Laya.
    Dictionary resolution and mandatory checks are identical to the CLI.
    """
    try:
        for name, value, allowed in (
            ("lang", lang, ("auto", "en", "ru")),
            ("enforce", enforce, ("warn", "block")),
            ("laya_mode", laya_mode, ("required", "ci")),
        ):
            if value not in allowed:
                raise InputError(f"{name} must be one of {', '.join(allowed)}")
        if not isinstance(profile, str):
            raise InputError("profile must be a string")
        if laya_mode == "ci" and os.environ.get("CI") != "true":
            raise InputError("laya_mode ci requires CI=true")
        if dict_path is not None and not isinstance(dict_path, (str, PathLike)):
            raise InputError("dict_path must be a path")
        selected = load_profile(profile)
        if isinstance(text, str):
            _no_surrogate(text, "text")
            raw = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
            units, meta = [unit_from_text(raw, selected.markup)], Meta()
        elif isinstance(text, dict):
            try:
                encoded = json.dumps(text)
            except (TypeError, ValueError, RecursionError) as exc:
                raise InputError(f"input@1 is not JSON: {exc}") from None
            units, meta = parse_input_json(encoded)
        else:
            raise InputError("text must be a string or an input@1 document")
        return check_units(units, meta, selected, enforce=enforce, lang=lang,
                           laya_mode=laya_mode, dict_path=dict_path,
                           from_input_json=isinstance(text, dict))
    except (InputError, ConfigError, EngineError) as exc:
        raise TextLintError(str(exc)) from exc
