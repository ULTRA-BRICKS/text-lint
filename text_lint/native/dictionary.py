"""The owner's dictionary, DICT.json version 1, loaded as `pattern` rules `Dict.<id>`.

The file is `{version: 1, description, rules: [{id, pattern, flags, replacement, why,
only_if_cyrillic?}]}`; it is checked as tg-cli's `loadDictionary` checks it
(`features/cli/dict-gate.ts`), and fails closed the same way: a file that is there but broken
(JSON, shape, an unsupported version, one rule that does not compile) is a ConfigError naming
the file and the rule, never a silently smaller rule set. Only a missing file at the default
path switches the `Dict.*` rules off.

Path order: `--dict`, `TEXT_LINT_DICT`, `TG_DICT_PATH` (tg-cli's name, kept for its users),
then `~/.claude/DICT.json`. An empty value counts as unset.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from ..profiles import ConfigError, names
from . import NativeRule, pattern

SUPPORTED_VERSION = 1
_DEFAULT = Path(".claude") / "DICT.json"


def resolve_path(cli: str | None, env: Mapping[str, str]) -> tuple[Path, bool] | None:
    """(path, explicit) of the dictionary to load, or None when there is none: no explicit
    path and no file at the default one. An explicit path that does not exist is a
    ConfigError naming where it came from."""
    for source, value in (("--dict", cli), ("TEXT_LINT_DICT", env.get("TEXT_LINT_DICT")),
                          ("TG_DICT_PATH", env.get("TG_DICT_PATH"))):
        if value:
            path = Path(value).expanduser()
            if not path.exists():
                raise ConfigError(f"dictionary {path} (from {source}) does not exist")
            return path, True
    home = env.get("HOME")
    if not home:
        return None
    path = Path(home) / _DEFAULT
    return (path, False) if path.exists() else None


def load(path: Path, explicit: bool) -> list[NativeRule]:
    """The `Dict.*` rules of the dictionary at ``path``. A missing file is a ConfigError when
    the path was explicit, else no rules."""
    try:
        raw = path.read_text(encoding="utf-8-sig")  # a hand-edited file may carry a BOM
    except FileNotFoundError:
        if explicit:
            raise ConfigError(f"dictionary {path} does not exist") from None
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"dictionary {path} cannot be read: {exc}") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"dictionary {path} is not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ConfigError(f'dictionary {path} is invalid: the top level must be an object with a "rules" array')
    version = data.get("version")
    # A missing version is version 1; null or true is not (Python's True == 1).
    if "version" in data and (isinstance(version, bool) or version != SUPPORTED_VERSION):
        raise ConfigError(f'dictionary {path} has "version": {json.dumps(version)}, but text-lint only '
                          f"understands version {SUPPORTED_VERSION}")
    if not isinstance(data.get("rules"), list):
        raise ConfigError(f'dictionary {path} is invalid: "rules" must be an array')
    profiles = names()
    rules = []
    for index, item in enumerate(data["rules"]):
        try:
            rules.append(_rule(item, profiles))
        except ValueError as exc:
            label = f'"{item["id"]}"' if isinstance(item, dict) and isinstance(item.get("id"), str) \
                and item["id"] else f"#{index + 1}"
            raise ConfigError(f"dictionary {path} is invalid: rule {label}: {exc}") from None
    return rules


def _rule(item, profiles):
    if not isinstance(item, dict):
        raise ValueError("not an object")  # noqa: TRY004 - caught as a schema error above
    for key in ("id", "pattern", "replacement"):
        if not isinstance(item.get(key), str) or (key != "replacement" and not item[key]):
            raise ValueError(f'missing or non-string "{key}"')
    for key, kind, name in (("flags", str, "a string"), ("only_if_cyrillic", bool, "true or false")):
        if key in item and not isinstance(item[key], kind):
            raise ValueError(f'"{key}" must be {name}')
    params = {"pattern": item["pattern"], "flags": item.get("flags", "")}
    pattern.check(params)
    if item.get("only_if_cyrillic"):
        params["only_if_script"] = "cyrillic"
    # tg-cli never reads "why", so a value that is not a string is ignored, not an error.
    why = item.get("why") if isinstance(item.get("why"), str) and item["why"] else None
    message = "Banned wording '{match}'" + (f": {why}" if why else "")
    return NativeRule(id=f"Dict.{item['id']}", kind="pattern", severity="error", profiles=profiles,
                      message=message, params=params, examples={}, replacement=item["replacement"], why=why)
