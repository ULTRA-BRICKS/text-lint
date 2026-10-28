"""Stable in-process entry point for consumers of input@1 and report@1."""

import json
import os
from collections.abc import Mapping
from os import PathLike
from types import MappingProxyType
from typing import Literal, NotRequired, TypedDict

from .check import check_units
from .engines import EngineError
from .inputs import (
    InputError,
    Meta,
    _no_surrogate,
    parse_input_json,
    unit_from_api_text,
)
from .native.intent import sender_prose
from .profiles import ConfigError, load_profile

__all__ = ["InputDocument", "InputField", "InputMeta", "TextLintError", "check", "sender_prose"]


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


_ENVIRONMENT_TYPES = "environment must be a mapping of strings to strings"


def _snapshot(environment: object) -> Mapping[str, str]:
    """A read-only copy of ``environment`` that the operating system accepts as is.

    POSIX environment entries are ``NAME=value`` byte strings without NUL, so a name must be
    non-empty and contain no ``=`` or NUL, a value no NUL, and both must encode as the
    filesystem encoding does (surrogate-escaped bytes from ``os.environ`` do). Anything else
    would fail later in a subprocess or socket call, after other effects. Diagnostics name
    the position of an invalid name and never repeat a value.
    """
    if not isinstance(environment, Mapping):
        raise InputError(_ENVIRONMENT_TYPES)
    snapshot = dict(environment)
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in snapshot.items()):
        raise InputError(_ENVIRONMENT_TYPES)
    for position, (name, value) in enumerate(snapshot.items(), 1):
        if not name or "=" in name or "\0" in name:
            raise InputError(f"environment name #{position} must be non-empty and contain no '=' or NUL")
        if "\0" in value:
            raise InputError(f"environment value for {name!r} contains NUL")
        try:
            os.fsencode(name)
        except UnicodeEncodeError:
            raise InputError(f"environment name #{position} cannot be encoded for the operating system") from None
        try:
            os.fsencode(value)
        except UnicodeEncodeError:
            raise InputError(f"environment value for {name!r} cannot be encoded for the operating system") from None
    return MappingProxyType(snapshot)


def check(text: str | InputDocument, *, profile: str = "prose",
          lang: Literal["auto", "en", "ru"] = "auto",
          enforce: Literal["block", "warn"] = "block",
          laya_mode: Literal["required", "ci"] = "required",
          dict_path: str | PathLike[str] | None = None,
          environment: Mapping[str, str] | None = None) -> dict:
    """Return report@1 without printing or exiting; raise TextLintError for exit-2 cases.

    Strings use the profile's markup and default sender metadata. Documents preserve
    input@1 fields and metadata. ``ci`` requires CI=true and never starts Laya.
    ``environment=None`` uses the process environment as the CLI does. A supplied mapping
    is copied at entry, replaces the environment for this check (including subprocesses),
    and never changes os.environ. An empty mapping inherits no process settings.
    Dictionary resolution and mandatory checks retain the CLI's defaults.
    """
    try:
        if environment is not None:
            environment = _snapshot(environment)
        env = os.environ if environment is None else environment
        for name, value, allowed in (
            ("lang", lang, ("auto", "en", "ru")),
            ("enforce", enforce, ("warn", "block")),
            ("laya_mode", laya_mode, ("required", "ci")),
        ):
            if value not in allowed:
                raise InputError(f"{name} must be one of {', '.join(allowed)}")
        if not isinstance(profile, str):
            raise InputError("profile must be a string")
        if laya_mode == "ci" and env.get("CI") != "true":
            raise InputError("laya_mode ci requires CI=true")
        if dict_path is not None and not isinstance(dict_path, (str, PathLike)):
            raise InputError("dict_path must be a path")
        selected = load_profile(profile)
        if isinstance(text, str):
            _no_surrogate(text, "text")
            units, meta = [unit_from_api_text(text, selected.markup)], Meta()
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
                           from_input_json=isinstance(text, dict), environment=environment)
    except (InputError, ConfigError, EngineError) as exc:
        raise TextLintError(str(exc)) from exc
