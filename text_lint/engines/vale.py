"""Vale (English, plus our TextLint pack for both languages), run with the vendored config in
text_lint/packs/vale. The extension a profile names picks the style set.

Every text is written to its own temporary file and all of them go to one Vale process: one
field's unclosed code fence or `<!-- vale off -->` must not hide the fields after it, which
joining them into one Markdown document would do.
"""

import json
import shutil
import tempfile
from pathlib import Path

from ..report import SEVERITIES, Finding
from ..text import LineIndex
from . import MALFORMED, EngineError, explicit_path, missing, string, unexpected
from . import run as run_engine

VALE_CONFIG = Path(__file__).resolve().parent.parent / "packs" / "vale" / "vale.ini"
VALE_INSTALL = "brew install vale  (or a release binary: https://vale.sh/docs/install)"
ENV = ("TEXT_LINT_VALE", "SLOP_CHECK_VALE")


def find_vale():
    explicit, path = explicit_path(*ENV)
    return path if explicit else shutil.which("vale")


def run(texts):
    """Vale findings for each ``(text, ext)`` of ``texts``, the text read as a file with
    extension ``ext``; offsets, lines and columns are within that text. EngineMissing when
    Vale is not installed."""
    path = find_vale()
    if path is None:
        raise missing("vale", ENV, "not found on PATH (the English engine)", VALE_INSTALL)
    with tempfile.TemporaryDirectory(prefix="text-lint-") as tmp:
        files = []
        for i, (text, ext) in enumerate(texts):
            file = Path(tmp) / f"unit-{i}{ext}"
            # newline="": the offsets are counted on "\n" line ends. A lone surrogate (valid
            # JSON, not UTF-8) becomes one "?", so offsets still hold.
            with open(file, "w", encoding="utf-8", errors="replace", newline="") as fh:
                fh.write(text)
            files.append(str(file))
        proc = run_engine("vale", [path, "--config", str(VALE_CONFIG), "--no-global", "--output=JSON",
                                   *files], "")
    # Vale exits 1 when an error-level alert is found; 2 and up is a runtime failure.
    if proc.returncode not in (0, 1):
        raise EngineError("vale", f"failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        report = json.loads(proc.stdout or "{}")
        unknown = set(report) - set(files)
        if unknown:
            raise ValueError(f"alerts for files it was not given: {sorted(unknown)}")
        return [_vale_findings(report.get(file, []), LineIndex(text)) for file, (text, _) in zip(files, texts)]
    except MALFORMED as exc:
        raise EngineError("vale", unexpected(exc, proc.stdout)) from None


def _vale_findings(alerts, index):
    findings = []
    for alert in alerts:
        start, end = _vale_span(alert, index)
        line, col = index.line_col(start)
        params = (alert.get("Action") or {}).get("Params") or []
        severity = string(alert.get("Severity", "warning"))
        if severity not in SEVERITIES:
            raise ValueError(f"unknown severity {severity!r}")
        findings.append(Finding(
            rule_id=string(alert["Check"]), engine="vale", severity=severity,
            line=line, col=col, span=(start, end), match=string(alert.get("Match", "")),
            message=string(alert.get("Message", "")), suggestion=" | ".join(map(string, params)) or None,
        ))
    return findings


def _vale_span(alert, index):
    """[start, end) offsets of a Vale alert. Vale gives Line and Span = [first, last] 1-based
    character columns of the source. Match is the rendered text, so it can differ from the
    source (``Great **question**``, ``Great [question](url)`` both match as ``Great question``):
    the source columns are trusted whenever they form a range on the line. When they do not
    (a multi-line match), Match is aligned to the source from the alert's start."""
    line = alert["Line"]
    first, last = alert["Span"]
    start = index.offset(line, first)
    match = alert.get("Match", "")
    if match and index.text[start:start + len(match)] == match:
        return start, start + len(match)
    if last >= first:
        try:
            return start, index.offset(line, last) + 1
        except IndexError:
            if not match:
                raise
    return start, _align(index.text, start, match)


_MARKUP = set("*_~`[]<>#")


def _align(text, start, match):
    """End of the source region from ``start`` that renders as ``match``: whitespace and
    inline markup are skipped, link targets "(...)" after "]" too. Never searches past a
    character that differs, so a finding cannot move onto a later occurrence (in code)."""
    i = start
    for ch in match:
        if ch.isspace():
            continue
        while i < len(text) and text[i] != ch:
            if text[i].isspace() or text[i] in _MARKUP:
                i += 1
            elif text[i] == "(" and i > start and text[i - 1] == "]":
                close = text.find(")", i)
                i = close + 1 if close != -1 else len(text)
            else:
                return min(start + len(match), len(text))
        if i == len(text):
            return len(text)
        i += 1
    return i
