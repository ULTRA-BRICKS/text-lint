"""text-lint command line: `check` reads text, runs the check, prints findings and exits
0/1/2; `install-skill` writes the agent blurb."""

import argparse
import json
import re
import sys

from . import __version__
from .check import check, exit_code
from .engines import CONTEXTS, EngineError
from .install import TOOL, install_skill

DESCRIPTION = ("Flag AI tells (stock phrases, flattery, hedging, agent status reports) "
               "in English and Russian prose.")

CHECK_EPILOG = """\
exit codes:
  0  no findings (an advisory Laya score never counts)
  1  findings
  2  usage error, unreadable input, or a needed engine missing or failed

engines: vale (English + TextLint rules; brew install vale) and aiw-ru (Russian;
installed with text-lint, or uv tool install aiw-ru). SLOP_CHECK_VALE / SLOP_CHECK_AIWRU
override their paths.
"""


def _parser():
    p = argparse.ArgumentParser(prog="text-lint", description=DESCRIPTION)
    p.add_argument("--version", action="version", version=f"text-lint {__version__}")
    commands = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    c = commands.add_parser(
        "check", help="check a file or stdin", description=DESCRIPTION, epilog=CHECK_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    c.add_argument("path", nargs="?", default="-", help="file to check; '-' or nothing reads stdin")
    c.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")
    c.add_argument("--lang", choices=("auto", "en", "ru"), default="auto",
                   help="auto routes each paragraph by its share of Cyrillic letters (default: auto)")
    c.add_argument("--context", choices=[name for name in CONTEXTS if name],
                   help="where the text goes: issue, pr (adds commit-message rules), tg (Telegram), docs")
    c.add_argument("--laya", action="store_true",
                   help="ask the already-running task-cli Laya server for an advisory score")
    c.add_argument("--ignore", action="append", default=[], metavar="RULE_ID",
                   help="drop a rule, e.g. ai-tells.EmDashUsage, or a whole style (ai-tells); repeatable")
    blurb = f"the agent blurb ~/.agents/skills/.blurbs/{TOOL}.md"
    commands.add_parser("install-skill", help=f"write {blurb}", description=f"Write {blurb}.")
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
    args = _parser().parse_args(argv)
    if args.command == "install-skill":
        try:
            return install_skill()
        except OSError as exc:
            print(_safe(f"text-lint: install-skill failed: {exc}"), file=sys.stderr)
            return 2
    label = "<stdin>" if args.path == "-" else args.path
    try:
        text = _read(args.path)
    except (OSError, UnicodeDecodeError) as exc:
        print(_safe(f"text-lint: cannot read {label}: {exc}"), file=sys.stderr)
        return 2
    try:
        report = check(text, lang=args.lang, context=args.context, ignore=args.ignore, use_laya=args.laya)
    except EngineError as exc:
        print(_safe(f"text-lint: {exc}"), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_text(report, label))
    return exit_code(report)
