"""report@1: the Finding shape, the report document, its verdict and the exit code.

The schema is text_lint/schema/report-v1.json. Exit codes: 0 for a `pass` or `warn` verdict,
1 for `block`; 2 (usage, input, configuration or engine errors) is decided by the CLI.
"""

from dataclasses import asdict, dataclass
from typing import Literal

from . import __version__

SCHEMA_ID = "text-lint/report@1"
SEVERITIES = ("suggestion", "warning", "error")
_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
# Laya's slop score is reported and never counts toward the verdict.
ADVISORY_RULES = frozenset({"laya.SlopProbability"})

Severity = Literal["error", "warning", "suggestion"]


@dataclass
class Finding:
    rule_id: str
    engine: str
    severity: Severity
    line: int
    col: int
    span: tuple[int, int]
    match: str
    message: str
    suggestion: str | None = None
    field: str | None = None
    path: str | None = None
    key: str | None = None
    # False for a finding in a `"gate": false` input field: reported, never blocks.
    gate: bool = True

    def as_dict(self):
        data = asdict(self)
        del data["gate"]
        data["span"] = list(self.span)
        return data


def at_least(severity, floor):
    """Whether ``severity`` is ``floor`` or more severe."""
    return _RANK[severity] >= _RANK[floor]


def capped(severity, cap):
    """``severity`` lowered to ``cap`` when it is more severe; ``cap=None`` keeps it."""
    return cap if cap is not None and _RANK[severity] > _RANK[cap] else severity


def verdict(findings, fail_on, enforce):
    """`block` when ``enforce`` is `block` and a gating finding is at or above ``fail_on``;
    `warn` when anything but advisory output was found; else `pass`."""
    counted = [f for f in findings if f.rule_id not in ADVISORY_RULES]
    if not counted:
        return "pass"
    if enforce == "block" and any(f.gate and at_least(f.severity, fail_on) for f in counted):
        return "block"
    return "warn"


def build_report(*, profile, enforce, findings, suppressed, engines, score, intent=None):
    """The report@1 dict. ``profile`` is the Profile the run used; its ``fail_on`` decides the
    verdict."""
    report = {
        "schema": SCHEMA_ID,
        "version": __version__,
        "profile": profile.name,
        "enforce": enforce,
        "verdict": verdict(findings, profile.fail_on, enforce),
        "score": score,
        "findings": [f.as_dict() for f in findings],
        "suppressed": list(suppressed),
        "engines": engines,
    }
    if intent is not None:
        report["intent"] = intent
    return report


def exit_code(report):
    return 1 if report["verdict"] == "block" else 0
