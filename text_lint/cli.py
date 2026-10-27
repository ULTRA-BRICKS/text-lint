"""text-lint command line: `check` reads text or an input@1 document, runs one profile over
it, prints the findings and exits by the verdict; `install-skill` writes the agent blurb."""

import argparse
import json
import os
import re
import sys

from . import __version__
from .check import check_units
from .engines import EngineError
from .inputs import InputError, Meta, parse_input_json, unit_from_text
from .install import TOOL, install_skill
from .profiles import ConfigError, load_profile, names
from .report import SEVERITIES, exit_code

DESCRIPTION = ("Flag AI tells (stock phrases, flattery, hedging, agent status reports) "
               "in English and Russian prose.")

CHECK_EPILOG = """\
exit codes:
  0  verdict pass or warn
  1  verdict block: --enforce block and a finding at or above the profile's fail_on
     (an advisory Laya score never counts)
  2  usage error, unreadable or invalid input, invalid profile or dictionary, a
     needed engine missing or failed, or Laya unreachable

engines: vale (English + TextLint rules; brew install vale) and aiw-ru (Russian;
installed with text-lint, or uv tool install aiw-ru). TEXT_LINT_VALE / TEXT_LINT_AIWRU
override their paths. Every profile also asks the local Laya classify server, once per run;
when no socket answers, `task classify-server start` (task-cli) starts it, and Laya still
unreachable exits 2 (an automated message is reported as laya: unreachable instead).
TEXT_LINT_LAYA_SOCKET names a socket (never started), TEXT_LINT_LAYA_TIMEOUT_MS the request
timeout. Native rules run in-process; the Dict.* rules come from the owner's dictionary:
--dict, else TEXT_LINT_DICT, else TG_DICT_PATH, else ~/.claude/DICT.json. A broken
dictionary, or a missing one that was named, exits 2; a missing default file means no
Dict.* rules.
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
    selection = c.add_mutually_exclusive_group()
    selection.add_argument("--context", dest="profile",
                           type=lambda value: {"docs": "prose", "tg": "tg-bot"}.get(value, value),
                           choices=("prose", "issue", "pr", "tg-bot"), default=argparse.SUPPRESS,
                           help="legacy profile alias (docs, issue, pr, tg)")
    selection.add_argument("--profile", choices=names(), default="prose",
                           help="what kind of text this is; picks engines, rules and fail_on (default: prose)")
    c.add_argument("--input-json", action="store_true",
                   help="read an input@1 document (named fields plus meta) instead of raw text")
    c.add_argument("--format", choices=("text", "json"), default="text", help="output format (default: text)")
    c.add_argument("--lang", choices=("auto", "en", "ru"), default="auto",
                   help="auto routes each paragraph by its share of Cyrillic letters (default: auto)")
    c.add_argument("--enforce", choices=("warn", "block"), default="block",
                   help="block: findings at or above fail_on exit 1; warn: report only (default: block)")
    c.add_argument("--fail-on", choices=SEVERITIES[::-1],
                   help="lowest severity that blocks (default: the profile's)")
    c.add_argument("--ignore", action="append", default=[], metavar="RULE_ID",
                   help="drop a rule, e.g. ai-tells.EmDashUsage, or a whole style (ai-tells); repeatable")
    c.add_argument("--laya-mode", choices=("required", "ci"), default="required",
                   help="ci: on a CI runner (CI=true only) ask Laya when a socket answers, else report it "
                        "as not run, never starting a server (default: required)")
    c.add_argument("--dict", metavar="PATH",
                   help="the owner's dictionary (DICT.json) for the Dict.* rules "
                        "(default: TEXT_LINT_DICT, TG_DICT_PATH, ~/.claude/DICT.json)")
    selftest = commands.add_parser("selftest", help="check packaged rule examples without Laya")
    selftest.add_argument("--rules", default="*", metavar="GLOB")
    blurb = f"the agent blurb ~/.agents/skills/.blurbs/{TOOL}.md"
    commands.add_parser("install-skill", help=f"write {blurb}", description=f"Write {blurb}.")
    return p, c


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
        where = f"{label}[{f['field']}]" if f.get("field") else label
        line = f"{where}:{f['line']}:{f['col']} {f['rule_id']} {f['message']}"
        if f["suggestion"]:
            line += f" → {f['suggestion']}"
        lines.append(_one_line(line))
    count = len(report["findings"])
    summary = (f"score: {report['score']}/100, {count} finding{'' if count == 1 else 's'}, "
               f"verdict: {report['verdict']} (profile {report['profile']})")
    skipped = [f"{name} skipped ({info['reason']})" for name, info in report["engines"].items()
               if info["status"] == "skipped" and name != "laya"]
    if skipped:
        summary += "; " + "; ".join(skipped)
    lines.append(_one_line(summary))
    advice = report["engines"].get("laya", {})
    if advice.get("status") == "ok":
        lines.append("laya: " + ", ".join(f"p({q})=" + ("n/a" if p is None else f"{p:.2f}")
                                          for q, p in advice["answers"].items()))
    elif advice.get("status") in ("unreachable", "not run"):
        lines.append(_one_line(f"laya: {advice['status']} ({advice['reason']})"))
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser, check_parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "selftest":
        from .selftest import run
        try:
            failed = run(args.rules)
        except (ConfigError, EngineError) as exc:
            print(_safe(f"text-lint: {exc}"), file=sys.stderr)
            return 2
        print("\n".join(failed) if failed else "Rule examples passed.")
        return 1 if failed else 0
    if args.command == "install-skill":
        try:
            return install_skill()
        except OSError as exc:
            print(_safe(f"text-lint: install-skill failed: {exc}"), file=sys.stderr)
            return 2
    if args.laya_mode == "ci" and os.environ.get("CI") != "true":
        check_parser.error("--laya-mode ci is for CI runners and needs CI=true in the environment")
    label = "<stdin>" if args.path == "-" else args.path
    try:
        raw = _read(args.path)
    except (OSError, UnicodeDecodeError) as exc:
        print(_safe(f"text-lint: cannot read {label}: {exc}"), file=sys.stderr)
        return 2
    try:
        profile = load_profile(args.profile)
        if args.input_json:
            units, meta = parse_input_json(raw)
        else:
            units, meta = [unit_from_text(raw, profile.markup)], Meta()
        report = check_units(units, meta, profile, enforce=args.enforce, lang=args.lang,
                             ignore=args.ignore, fail_on=args.fail_on, laya_mode=args.laya_mode,
                             dict_path=args.dict, from_input_json=args.input_json)
    except InputError as exc:
        print(_safe(f"text-lint: {label}: {exc}"), file=sys.stderr)
        return 2
    except (ConfigError, EngineError) as exc:
        print(_safe(f"text-lint: {exc}"), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_format_text(report, label))
    return exit_code(report)
