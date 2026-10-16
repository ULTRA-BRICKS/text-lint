"""The two engines slop-check wraps, run as subprocesses and mapped to one Finding shape.

Vale (English, plus our SlopCheck pack for both languages) runs with the vendored config in
slop_cli/vale; aiw-ru (Russian) runs `aiw-ru scan --json`. Both read the text on stdin.
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

VALE_CONFIG = Path(__file__).resolve().parent / "vale" / "vale.ini"
VALE_INSTALL = "brew install vale  (or a release binary: https://vale.sh/docs/install)"
AIWRU_INSTALL = "uv tool install aiw-ru  (or reinstall slop-check, which depends on it)"
ENGINE_TIMEOUT_S = 120

# --context -> (Vale extension, aiw-ru --context). The extensions are mapped to Markdown in
# vale.ini; .pr also turns on the ai-tells-commits style.
CONTEXTS = {
    None: (".md", "general"),
    "issue": (".md", "technical"),
    "pr": (".pr", "technical"),
    "tg": (".tg", "telegram"),
    "docs": (".md", "docs"),
}
_AIWRU_SEVERITY = {"P0": "error", "P1": "warning", "P2": "suggestion"}


@dataclass
class Finding:
    rule_id: str
    engine: str
    severity: str
    line: int
    col: int
    span: tuple
    match: str
    message: str
    suggestion: str = ""

    def as_dict(self):
        data = asdict(self)
        data["span"] = list(self.span)
        return data


class EngineError(Exception):
    """A needed engine is missing or failed; the CLI exits 2 with this message."""

    def __init__(self, engine, problem, install=None):
        self.engine = engine
        message = f"{engine}: {problem}"
        if install:
            message += f"\n  install: {install}"
        super().__init__(message)


def _explicit(env_name):
    """An engine path set in the environment: used as is, never replaced by a PATH lookup."""
    value = os.environ.get(env_name)
    if value is None:
        return False, None
    return True, value if os.path.isfile(value) and os.access(value, os.X_OK) else None


def _missing(engine, env_name, default_problem, install):
    value = os.environ.get(env_name)
    if value is not None:
        return EngineError(engine, f"{env_name}={value} is not an executable file", install)
    return EngineError(engine, default_problem, install)


def find_vale():
    explicit, path = _explicit("SLOP_CHECK_VALE")
    return path if explicit else shutil.which("vale")


def find_aiwru():
    explicit, path = _explicit("SLOP_CHECK_AIWRU")
    if explicit:
        return path
    # Installed as our dependency (pipx / uv tool venv): its script sits next to our python.
    # On Windows that script is Scripts/aiw-ru.exe.
    for name in ("aiw-ru", "aiw-ru.exe"):
        sibling = Path(sys.executable).parent / name
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    return shutil.which("aiw-ru")


def _run(engine, argv, text):
    try:
        # errors="replace": a stray undecodable byte must not crash the run (exit 1 would
        # read as "findings"); the JSON parse below then reports a broken engine instead.
        proc = subprocess.run(argv, input=text, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=ENGINE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise EngineError(engine, f"no answer within {ENGINE_TIMEOUT_S}s") from None
    except OSError as exc:
        raise EngineError(engine, f"could not start {argv[0]}: {exc}") from None
    return proc


def vale_version():
    path = find_vale()
    if path is None:
        return None
    out = _run("vale", [path, "--version"], "").stdout.strip()
    return out.rsplit(" ", 1)[-1] if out else None


def run_vale(text, context, index):
    """Vale findings on ``text``. ``index`` is the LineIndex of ``text``."""
    path = find_vale()
    if path is None:
        raise _missing("vale", "SLOP_CHECK_VALE", "not found on PATH (the English engine)", VALE_INSTALL)
    ext = CONTEXTS[context][0]
    proc = _run("vale", [path, "--config", str(VALE_CONFIG), "--no-global", "--output=JSON",
                         f"--ext={ext}"], text)
    # Vale exits 1 when an error-level alert is found; 2 and up is a runtime failure.
    if proc.returncode not in (0, 1):
        raise EngineError("vale", f"failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return _vale_findings(json.loads(proc.stdout or "{}"), index)
    except _MALFORMED as exc:
        raise EngineError("vale", _unexpected(exc, proc.stdout)) from None


# What reading an engine's JSON raises when the output does not have the documented shape.
_MALFORMED = (ValueError, KeyError, TypeError, AttributeError, IndexError)


def _unexpected(exc, stdout):
    return f"unexpected output ({type(exc).__name__}: {exc}): {stdout[:200]!r}"


def _str(value):
    """``value`` if it is a string; anything else is malformed engine output."""
    if not isinstance(value, str):
        raise TypeError(f"expected a string, got {value!r}")
    return value


def _vale_findings(report, index):
    findings = []
    for alerts in report.values():
        for alert in alerts:
            start, end = _vale_span(alert, index)
            line, col = index.line_col(start)
            params = (alert.get("Action") or {}).get("Params") or []
            findings.append(Finding(
                rule_id=_str(alert["Check"]), engine="vale", severity=_str(alert.get("Severity", "warning")),
                line=line, col=col, span=(start, end), match=_str(alert.get("Match", "")),
                message=_str(alert.get("Message", "")), suggestion=" | ".join(map(_str, params)),
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


def run_aiwru(text, context, index):
    """(findings, score, label) from aiw-ru on ``text``; ``styleOnly`` advice is dropped."""
    path = find_aiwru()
    if path is None:
        raise _missing("aiw-ru", "SLOP_CHECK_AIWRU", "not found (the Russian engine)", AIWRU_INSTALL)
    proc = _run("aiw-ru", [path, "scan", "--json", "--context", CONTEXTS[context][1]], text)
    if proc.returncode != 0:
        raise EngineError("aiw-ru", f"failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        report = json.loads(proc.stdout)
        return _aiwru_findings(report, text, index), int(report.get("score") or 0), str(report.get("label", ""))
    except _MALFORMED as exc:
        raise EngineError("aiw-ru", _unexpected(exc, proc.stdout)) from None


def _aiwru_findings(report, text, index):
    findings = []
    for issue in report["issues"]:
        if issue.get("styleOnly"):
            continue
        quoted = _str(issue["text"])
        start = issue.get("index", -1)
        if not isinstance(start, int) or start < 0 or text[start:start + len(quoted)] != quoted:
            start = index.offset(issue["line"], issue["column"])
        if text[start:start + len(quoted)] == quoted:
            match = quoted
            message = f"Признак ИИ-стиля: «{match}» ({issue['type']})"
        else:
            # Some issue types describe what they found ("1 неразрывный пробел (U+00A0)")
            # instead of quoting it; the position then points at the first such character.
            match = text[start:start + 1]
            message = f"Признак ИИ-стиля: {quoted} ({issue['type']})"
        line, col = index.line_col(start)
        findings.append(Finding(
            rule_id=f"aiwru.{issue['type']}.{issue['rule']}", engine="aiw-ru",
            severity=_AIWRU_SEVERITY.get(issue.get("severity"), "warning"),
            line=line, col=col, span=(start, start + len(match)), match=match,
            message=message, suggestion=_str(issue.get("hint") or ""),
        ))
    return findings
