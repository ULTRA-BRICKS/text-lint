"""Native rules: code-backed kinds driven by YAML rule files (spec section 4.6).

A rule file in ``packs/native/<pack>/`` names a ``kind``, its ``params`` and its own examples,
and is validated against ``schema/native-rule-v1.json``. ``KINDS`` maps each kind to the
function that runs one rule on one unit: ``kind(rule, unit, meta, whole_text) -> list[Finding]``.
The engine supplies its batched RunContext internally through ``run_kind``. ``CHECKS`` holds
a kind's load-time check of its params (a regex that does not compile must fail the load, not the first run).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..inputs import Meta, Unit
from ..profiles import ConfigError, names
from ..report import Finding

PACK_DIR = Path(__file__).resolve().parent.parent / "packs" / "native"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "native-rule-v1.json"


@dataclass
class NativeRule:
    id: str
    kind: str
    severity: str
    profiles: list[str]
    message: str
    params: dict
    examples: dict[str, list[str]]
    replacement: str | None = None
    why: str | None = None


@dataclass
class RunContext:
    """What a kind may read beyond its own unit. ``gating_text`` is the raw text of every
    gating unit, joined: a script condition (``only_if_script``) is judged on it, while a
    `"gate": false` unit is judged on its own raw text. ``laya`` holds the Laya answers of
    the run (question -> probability); ``from_input_json`` is false for a file or stdin.
    ``message`` is the unit the message kinds (`format`, `intent`, `long-unstructured`) read
    (message_unit), and ``intent`` the run's intent verdict (intent.of_message)."""

    units: list[Unit]
    meta: Meta
    gating_text: str
    laya: dict[str, float | None] = field(default_factory=dict)
    from_input_json: bool = False
    message: Unit | None = None
    intent: "intent.Intent | None" = None


# The message body among input@1 fields, in order of preference (spec 4.6).
MESSAGE_FIELDS = ("body", "caption")


def message_unit(units: list[Unit], *, from_input_json: bool) -> Unit | None:
    """The unit the message kinds read: the field named `body`, else `caption`; for a file
    or stdin (one unit, no field name) that unit. A title, label or generated title never
    is."""
    if not from_input_json:
        return units[0] if len(units) == 1 else None
    for name in MESSAGE_FIELDS:
        for unit in units:
            if unit.field == name:
                return unit
    return None


def finding(rule, unit, start, end, *, space, match):
    """A Finding of ``rule`` for ``unit`` at [start, end), offsets into ``unit.text``
    (``space="text"``) or ``unit.raw`` (``space="raw"``); line, col and span point into the
    field as sent."""
    line, col = unit.position(start, space=space)
    span = unit.raw_span(start, end) if space == "text" else (start, end)
    return Finding(rule_id=rule.id, engine="native", severity=rule.severity, line=line, col=col, span=span,
                   match=match, message=rule.message.replace("{match}", match), suggestion=rule.replacement,
                   field=unit.field, path=unit.path, key=unit.key, gate=unit.gate)


def load_pack(directory: Path) -> list[NativeRule]:
    """The rules of one pack directory (``*.yml``, one rule per file), each validated against
    native-rule-v1.json and its kind's params check; ConfigError names the file."""
    import jsonschema  # only a run with installed packs pays for the import

    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    known_profiles = set(names())
    rules = []
    seen = {}
    for path in sorted(Path(directory).glob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ConfigError(f"{path}: {exc}") from None
        error = jsonschema.exceptions.best_match(validator.iter_errors(data))
        if error is not None:
            where = "/".join(map(str, error.absolute_path)) or "rule"
            raise ConfigError(f"{path}: {where}: {error.message}")
        if data["kind"] not in KINDS:
            raise ConfigError(f"{path}: unknown kind {data['kind']!r}; kinds: {', '.join(sorted(KINDS))}")
        unknown = sorted(set(data["profiles"]) - known_profiles)
        if unknown:
            raise ConfigError(f"{path}: unknown profile(s) {', '.join(unknown)}")
        try:
            CHECKS.get(data["kind"], lambda params: None)(data["params"])
        except ValueError as exc:
            raise ConfigError(f"{path}: rule {data['id']}: {exc}") from None
        if data["id"] in seen:
            raise ConfigError(f"{path}: rule id {data['id']} is already used by {seen[data['id']]}")
        seen[data["id"]] = path
        rules.append(NativeRule(id=data["id"], kind=data["kind"], severity=data["severity"],
                                profiles=data["profiles"], message=data["message"], params=data["params"],
                                examples=data["examples"], replacement=data.get("replacement"),
                                why=data.get("why")))
    return rules


# The kind modules build their findings with ``finding`` above, so they register last.
from . import as_user, formats, intent, pattern, script, spoken, structure

Kind = Callable[[NativeRule, Unit, Meta, str], list[Finding]]


class _KindAdapter:
    """Expose the planned four-argument API without rebuilding an engine run's state."""

    def __init__(self, implementation):
        self.with_context = implementation

    def __call__(self, rule, unit, meta, whole_text):
        # A standalone call has one known unit and no Laya response. Preserve message
        # field selection (body/caption, never a title) and sender metadata.
        from ..engines.native import context

        ctx = context([unit], meta, from_input_json=unit.field is not None)
        ctx.gating_text = whole_text
        return self.with_context(rule, unit, ctx)


def run_kind(rule, unit, ctx):
    kind = KINDS[rule.kind]
    if isinstance(kind, _KindAdapter):
        return kind.with_context(rule, unit, ctx)
    return kind(rule, unit, ctx.meta, ctx.gating_text)


KINDS: dict[str, Kind] = {name: _KindAdapter(fn) for name, fn in {
    "pattern": pattern.run, "stray-cjk": script.stray_cjk, "mixed-script": script.mixed_script,
    "as-user-emoji": as_user.emoji, "bot-formatting": as_user.formatting,
    "first-person": as_user.first_person, "spoken-markup": spoken.run,
    "format": formats.run, "intent": intent.run, "long-unstructured": structure.run,
}.items()}
CHECKS: dict[str, Callable[[dict], None]] = {"pattern": pattern.check}

CHECKS.update({name: as_user.no_params for name in
               ("as-user-emoji", "bot-formatting", "first-person", "spoken-markup")})
