"""Check packaged examples without reading a dictionary or contacting Laya.

Laya-derived rules use explicit fixture probabilities here. This tests their threshold
logic, not the availability or accuracy of a model. Vale samples use the real engine.
"""

from fnmatch import fnmatchcase
from pathlib import Path

from .engines import native, vale
from .inputs import Meta, unit_from_text
from .native import PACK_DIR, load_pack


def native_rules():
    return [rule for pack in sorted(PACK_DIR.iterdir()) if pack.is_dir()
            for rule in load_pack(pack)]


def run(rule_glob: str = "*") -> list[str]:
    """Return rule ids whose positive or negative examples fail their contract."""
    failed = set()
    for rule in native_rules():
        if not fnmatchcase(rule.id, rule_glob):
            continue
        for outcome, examples in rule.examples.items():
            for text in examples:
                meta = Meta(agent_authored=True)
                if rule.kind == "format":
                    meta.tag = rule.params["validator"]
                    meta.send_format = "html"
                fmt = "plain" if rule.kind == "long-unstructured" else "html"
                # Selftest fixtures only; never represented as a live engine status.
                answers = {"first_person": .3 if outcome == "flag" else .9}
                findings = native.run([unit_from_text(text, fmt)], meta, [rule], laya=answers)
                if any(f.rule_id == rule.id for f in findings) != (outcome == "flag"):
                    failed.add(rule.id)
    samples = Path(__file__).parent / "packs" / "vale" / "samples.tsv"
    cases = []
    for line in samples.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, text = line.split("\t", 1)
        rule_id = f"TextLint.{name}"
        if fnmatchcase(rule_id, rule_glob):
            cases.append((rule_id, text))
    if cases:
        results = vale.run([(text, ".md") for _, text in cases])
        for (rule_id, _), findings in zip(cases, results, strict=True):
            if not any(f.rule_id == rule_id for f in findings):
                failed.add(rule_id)
    return sorted(failed)
