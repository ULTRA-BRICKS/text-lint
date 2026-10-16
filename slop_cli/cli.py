"""slop-check command line: read text, run the check, print findings, exit 0/1/2."""

import argparse
import json
import re
import sys

from . import __version__
from .check import check, exit_code
from .engines import CONTEXTS, EngineError

EPILOG = """\
exit codes:
  0  no findings (an advisory Laya score never counts)
  1  findings
  2  usage error, unreadable input, or a needed engine missing or failed

`slop-check install-skill` writes the agent blurb ~/.agents/skills/.blurbs/slop-check.md.

engines: vale (English + SlopCheck rules; brew install vale) and aiw-ru (Russian;
installed with slop-check, or uv tool install aiw-ru). SLOP_CHECK_VALE / SLOP_CHECK_AIWRU
override their paths.
"""


def _parser():
    p = argparse.ArgumentParser(
        prog="slop-check",
        description="Flag AI tells (stock phrases, flattery, hedging, agent status reports) "
                    "in English and Russian prose.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", default="-", help="file to check; '-' or nothing reads stdin")
    p.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")
    p.add_argument("--lang", choices=("auto", "en", "ru"), default="auto",
                   help="auto routes each paragraph by its share of Cyrillic letters (default: auto)")
    p.add_argument("--context", choices=[c for c in CONTEXTS if c],
                   help="where the text goes: issue, pr (adds commit-message rules), tg (Telegram), docs")
    p.add_argument("--laya", action="store_true",
                   help="ask the already-running task-cli Laya server for an advisory score")
    p.add_argument("--ignore", action="append", default=[], metavar="RULE_ID",
                   help="drop a rule, e.g. ai-tells.EmDashUsage, or a whole style (ai-tells); repeatable")
    p.add_argument("--version", action="version", version=f"slop-check {__version__}")
    return p


def _read(path):
    # A leading BOM is dropped: aiw-ru reads stdin as utf-8-sig, so its offsets skip it.
    # Newlines are normalised for stdin too, as open() does for a file, so spans agree.
    if path == "-":
        return sys.stdin.read().removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


# Messages quote the checked text, which may carry terminal escapes (OSC 52 clipboard
# writes and the like) or line breaks; the text format prints each finding on one safe line.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _one_line(value):
    return _CONTROL.sub(lambda m: " " if m.group() in "\n\r\t" else f"\\x{ord(m.group()):02x}", value)


def _safe(message):
    """``message`` with control characters escaped line by line (paths and engine output
    can carry terminal escapes); its own line breaks stay."""
    return "\n".join(_one_line(line) for line in message.split("\n"))


def _format_text(report, label):
    lines = []
    for f in report["findings"]:
        line = f"{label}:{f['line']}:{f['col']} {f['rule_id']} {f['message']}"
        if f["suggestion"]:
            line += f" → {f['suggestion']}"
        lines.append(_one_line(line))
    count = len(report["findings"])
    summary = f"score: {report['score']}/100, {count} finding{'' if count == 1 else 's'}"
    skipped = [f"{name} skipped ({info['reason']})" for name, info in report["engines"].items()
               if info["status"] == "skipped"]
    if skipped:
        summary += "; " + "; ".join(skipped)
    lines.append(summary)
    advice = report["engines"]["laya"]
    if advice["status"] == "ok":
        lines.append(f"laya: p(slop)={advice['noul']:.2f} (advisory)")
    elif advice["status"] == "unavailable":
        lines.append("laya: unavailable")
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install-skill"]:
        # A subcommand next to the optional input path: check a file named so as ./install-skill.
        argparse.ArgumentParser(prog="slop-check install-skill",
                                description="Write the agent blurb ~/.agents/skills/.blurbs/slop-check.md.",
                                ).parse_args(argv[1:])
        from .install import install_skill
        try:
            return install_skill()
        except OSError as exc:
            print(_safe(f"slop-check: install-skill failed: {exc}"), file=sys.stderr)
            return 2
    args = _parser().parse_args(argv)
    label = "<stdin>" if args.path == "-" else args.path
    try:
        text = _read(args.path)
    except (OSError, UnicodeDecodeError) as exc:
        print(_safe(f"slop-check: cannot read {label}: {exc}"), file=sys.stderr)
        return 2
    try:
        report = check(text, lang=args.lang, context=args.context, ignore=args.ignore, use_laya=args.laya)
    except EngineError as exc:
        print(_safe(f"slop-check: {exc}"), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_text(report, label))
    return exit_code(report)
