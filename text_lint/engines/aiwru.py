"""aiw-ru (Russian), run as `aiw-ru scan --json --no-model --context <profile context>` on stdin.

`--no-model` keeps an installed aiw-ru model from ever loading: a check makes no network call
and stays within its memory budget.
"""

import json
import os
import shutil
import sys
from pathlib import Path

from ..report import Finding
from . import MALFORMED, EngineError, explicit_path, missing, string, unexpected
from . import run as run_engine

AIWRU_INSTALL = "uv tool install aiw-ru  (or reinstall text-lint, which depends on it)"
ENV = ("TEXT_LINT_AIWRU", "SLOP_CHECK_AIWRU")
_SEVERITY = {"P0": "error", "P1": "warning", "P2": "suggestion"}


def find_aiwru(*, env=None):
    explicit, path = explicit_path(*ENV, env=env)
    if explicit:
        return path
    if env is not None:
        return shutil.which("aiw-ru", path=env.get("PATH", ""))
    # Installed as our dependency (pipx / uv tool venv): its script sits next to our python.
    # On Windows that script is Scripts/aiw-ru.exe.
    for name in ("aiw-ru", "aiw-ru.exe"):
        sibling = Path(sys.executable).parent / name
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    return shutil.which("aiw-ru")


def run(text, context, index, *, env=None):
    """(findings, score, label) from aiw-ru on ``text``; ``styleOnly`` advice is dropped.
    EngineMissing when aiw-ru is not installed."""
    env_options = {} if env is None else {"env": env}
    path = find_aiwru(**env_options)
    if path is None:
        raise missing("aiw-ru", ENV, "not found (the Russian engine)", AIWRU_INSTALL, **env_options)
    proc = run_engine("aiw-ru", [path, "scan", "--json", "--no-model", "--context", context], text, **env_options)
    if proc.returncode != 0:
        raise EngineError("aiw-ru", f"failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        report = json.loads(proc.stdout)
        return _findings(report, text, index), int(report.get("score") or 0), str(report.get("label", ""))
    except MALFORMED as exc:
        raise EngineError("aiw-ru", unexpected(exc, proc.stdout)) from None


def _findings(report, text, index):
    findings = []
    for issue in report["issues"]:
        if issue.get("styleOnly"):
            continue
        quoted = string(issue["text"])
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
            severity=_SEVERITY.get(issue.get("severity"), "warning"),
            line=line, col=col, span=(start, start + len(match)), match=match,
            message=message, suggestion=string(issue.get("hint") or "") or None,
        ))
    return findings
