"""Profiles: text_lint/profiles/<name>.yaml names the engines, rule packs, markup and
`fail_on` of one kind of text (spec section 4.5).

A profile file:

    name: tg-bot              # the file name without .yaml
    fail_on: error            # lowest severity that blocks: error | warning | suggestion
    markup: markdown          # how a file or stdin is read: plain | markdown | html
    packs: [dict, script]     # native rule packs
    engines:                  # native, vale, aiw-ru, laya
      vale: {enabled: true, optional: true, cap: warning, options: {ext: .tg}}

An engine's `optional: true` reports it as skipped when it is not installed (else exit 2);
`cap` lowers its findings to that severity. Options: vale `ext` (the extension that picks the
style set in packs/vale/vale.ini) and `disable` (rule ids dropped); aiw-ru `context`; laya
`questions`, `max_chars`, `timeout_ms`, `start_wait_ms` (the only place the Laya numbers live).
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .report import SEVERITIES

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
ENGINES = ("native", "vale", "aiw-ru", "laya")
MARKUPS = ("plain", "markdown", "html")
LAYA_QUESTIONS = ("slop", "asks_reader", "first_person")
_ENGINE_KEYS = {"enabled", "optional", "cap", "options"}
_PROFILE_KEYS = {"name", "fail_on", "markup", "packs", "engines"}
_OPTION_TYPES = {
    "vale": {"ext": str, "disable": list},
    "aiw-ru": {"context": str},
    "laya": {"questions": list, "max_chars": int, "timeout_ms": int, "start_wait_ms": int},
    "native": {},
}


class ConfigError(Exception):
    """Invalid configuration (a profile here); the CLI exits 2 with this message."""


@dataclass
class EngineSpec:
    enabled: bool
    optional: bool = False
    cap: str | None = None
    options: dict = field(default_factory=dict)


@dataclass
class Profile:
    name: str
    engines: dict[str, EngineSpec]
    packs: list[str]
    fail_on: str
    markup: str


def names():
    return sorted(p.stem for p in PROFILE_DIR.iterdir() if p.suffix == ".yaml")


def load_profile(name):
    path = PROFILE_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(f"unknown profile {name!r}; profiles: {', '.join(names())}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    try:
        return _profile(name, data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _check(ok, problem):
    if not ok:
        raise ConfigError(problem)


def _profile(name, data):
    _check(isinstance(data, dict), "a profile is a mapping")
    _check(set(data) == _PROFILE_KEYS,
           f"keys must be {', '.join(sorted(_PROFILE_KEYS))}; got {', '.join(sorted(map(str, data)))}")
    _check(data["name"] == name, f"name must be {name!r} (the file name), not {data['name']!r}")
    _check(data["fail_on"] in SEVERITIES, f"fail_on must be one of {', '.join(SEVERITIES)}, not {data['fail_on']!r}")
    _check(data["markup"] in MARKUPS, f"markup must be one of {', '.join(MARKUPS)}, not {data['markup']!r}")
    packs = data["packs"]
    _check(isinstance(packs, list) and all(isinstance(p, str) for p in packs), "packs must be a list of names")
    engines = data["engines"]
    _check(isinstance(engines, dict), "engines must be a mapping")
    unknown = sorted(set(map(str, engines)) - set(ENGINES))
    _check(not unknown, f"unknown engine(s) {', '.join(unknown)}; engines: {', '.join(ENGINES)}")
    specs = {engine: _engine(engine, engines.get(engine, {"enabled": False})) for engine in ENGINES}
    return Profile(name=name, engines=specs, packs=packs, fail_on=data["fail_on"], markup=data["markup"])


def _engine(engine, data):
    _check(isinstance(data, dict) and "enabled" in data and set(data) <= _ENGINE_KEYS,
           f"engines.{engine} takes enabled (required), optional, cap, options")
    spec = EngineSpec(enabled=data["enabled"], optional=data.get("optional", False), cap=data.get("cap"),
                      options=data.get("options") or {})
    _check(isinstance(spec.enabled, bool) and isinstance(spec.optional, bool),
           f"engines.{engine}: enabled and optional are true or false")
    _check(spec.cap is None or spec.cap in SEVERITIES,
           f"engines.{engine}.cap must be one of {', '.join(SEVERITIES)}, not {spec.cap!r}")
    _check(isinstance(spec.options, dict), f"engines.{engine}.options must be a mapping")
    types = _OPTION_TYPES[engine]
    for key, value in spec.options.items():
        _check(key in types, f"engines.{engine}.options: unknown option {key!r}")
        _check(isinstance(value, types[key]) and not isinstance(value, bool)
               and (types[key] is not list or all(isinstance(item, str) for item in value)),
               f"engines.{engine}.options.{key} has the wrong type: {value!r}")
    if engine == "laya" and spec.enabled:
        missing = sorted(set(types) - set(spec.options))
        _check(not missing, f"engines.laya.options needs {', '.join(missing)}")
        bad = [q for q in spec.options["questions"] if q not in LAYA_QUESTIONS]
        _check(spec.options["questions"] and not bad,
               f"engines.laya.options.questions: unknown {bad!r}; questions: {', '.join(LAYA_QUESTIONS)}")
        for key in ("max_chars", "timeout_ms", "start_wait_ms"):
            _check(spec.options[key] > 0, f"engines.laya.options.{key} must be positive")
    if engine == "vale" and spec.enabled:
        _check("ext" in spec.options, "engines.vale.options needs ext")
    if engine == "aiw-ru" and spec.enabled:
        _check("context" in spec.options, "engines.aiw-ru.options needs context")
    return spec
