"""The native engine: the rules of the profile's packs (``packs/native/<pack>/``) and, for a
profile that lists ``dict``, the owner's dictionary, run in-process on every unit."""

import os

from ..native import (
    PACK_DIR,
    RunContext,
    dictionary,
    intent,
    load_pack,
    message_unit,
    run_kind,
)
from ..profiles import ConfigError

# Units of a run are joined with a blank line for the script condition, as in check.py.
_SEPARATOR = "\n\n"


def rules_for(profile, *, dict_path=None, env=None):
    """(rules, engine status) for ``profile``: its packs' rules that list the profile, plus
    `Dict.*` when it lists ``dict``. ``dict_path`` is `--dict`; ``env`` defaults to the
    process environment. ConfigError for a broken pack or dictionary."""
    env = os.environ if env is None else env
    rules = []
    info = {}
    missing = []
    for pack in profile.packs:
        if pack == "dict":
            location = dictionary.resolve_path(dict_path, env)
            info["dict"] = str(location[0]) if location else None
            if location:
                rules += dictionary.load(*location)
        elif (PACK_DIR / pack).is_dir():
            rules += [r for r in load_pack(PACK_DIR / pack) if profile.name in r.profiles]
        else:
            missing.append(pack)
    if missing:
        raise ConfigError(f"missing native pack(s) for {profile.name}: {', '.join(missing)}")
    if rules:
        return rules, {"status": "ok", "rules": len(rules), **info}
    return rules, {"status": "skipped", "reason": f"no native rules for profile {profile.name}", **info}


def context(units, meta, *, from_input_json=False, laya=None):
    """The RunContext every kind of one run reads: its message unit and the intent verdict
    for it are decided once per run."""
    units = list(units)
    laya = dict(laya or {})
    message = message_unit(units, from_input_json=from_input_json)
    return RunContext(units=units, meta=meta, gating_text=_SEPARATOR.join(u.raw for u in units if u.gate),
                      laya=laya, from_input_json=from_input_json, message=message,
                      intent=intent.of_message(message, meta, laya.get("asks_reader")))


def run_unit(unit, rules, ctx):
    """Every rule on ``unit``, by offset, then rule."""
    found = [f for rule in rules for f in run_kind(rule, unit, ctx)]
    return sorted(found, key=lambda f: (f.span[0], f.span[1], f.rule_id))


def run(units, meta, rules, *, from_input_json=False, laya=None):
    """Every rule on every unit; findings in unit order, then by offset, then rule."""
    ctx = context(units, meta, from_input_json=from_input_json, laya=laya)
    return [f for unit in ctx.units for f in run_unit(unit, rules, ctx)]
