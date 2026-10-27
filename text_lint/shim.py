"""`slop-check`, the 0.1 command name, kept while callers move to `text-lint`.

It prints one deprecation line to stderr and runs `text-lint check` with the 0.1 arguments
translated: `--context` becomes a profile (none or `docs` → `prose`, `issue`, `pr`, `tg` →
`tg-bot`) with `--fail-on suggestion`, so any finding exits 1 as in 0.1. Two differences come
with `tg-bot`: a missing Vale or aiw-ru is reported as skipped instead of exit 2, and emoji
rules (`TextLint.DoneEmoji`) are off. The dropped `--laya` switch is ignored (every profile
asks Laya); the renamed rule pack in `--ignore` is mapped
(`SlopCheck.X` → `TextLint.X`); 0.1 option abbreviations are spelled out, since text-lint has
options they would now be ambiguous with.
`slop-check --version` answers `slop-check <version>`, as 0.1 did.
`slop-check install-skill`, which older install.sh copies call, runs `text-lint install-skill`.
The shim goes once nothing calls `slop-check` (spec section 4.1).
"""

import sys

from . import __version__, cli

DEPRECATION = "slop-check is deprecated; use text-lint check"
_OLD_STYLE, _NEW_STYLE = "SlopCheck", "TextLint"
_OPTIONS = ("--format", "--lang", "--context", "--laya", "--ignore", "--version", "--help")
_TAKES_VALUE = {"--format", "--lang", "--context", "--ignore"}
_PROFILES = {None: "prose", "docs": "prose", "issue": "issue", "pr": "pr", "tg": "tg-bot"}


def _rule(value):
    if value == _OLD_STYLE or value.startswith(_OLD_STYLE + "."):
        return _NEW_STYLE + value[len(_OLD_STYLE):]
    return value


def _abbreviates(arg, option):
    """``arg`` names ``option`` as 0.1's argparse read it: any prefix down to `--i` / `--v`,
    which no other 0.1 option starts with."""
    return len(arg) >= 3 and option.startswith(arg)


def _option(name):
    """The 0.1 option ``name`` stands for (a unique prefix, as argparse matched it), or None."""
    hits = [option for option in _OPTIONS if _abbreviates(name, option)] if name.startswith("--") else []
    return hits[0] if len(hits) == 1 else None


def translate(argv):
    """The `text-lint` argv that does what ``argv`` did for slop-check 0.1."""
    argv = list(argv)
    if argv[:1] == ["install-skill"]:
        return argv
    # After `--` every argument is a path, as argparse reads it.
    options = argv[:argv.index("--")] if "--" in argv else argv
    # A version flag anywhere among the options wins, also in an argv 0.1 would have
    # rejected first (`--ignore --version`): a version probe never needs that error.
    if any(_abbreviates(arg, "--version") for arg in options):
        return ["--version"]
    context = None
    out = []
    pending = None  # an option whose value is the next argument
    for arg in options:
        if pending is not None:
            option, value, inline, pending = pending, arg, False, None
        else:
            name, eq, value = arg.partition("=")
            option = _option(name)
            if option is None or (eq and option not in _TAKES_VALUE):
                # Not a 0.1 option, or a flag given a value (0.1 rejected that too): text-lint's
                # parser decides.
                out.append(arg)
                continue
            if option == "--laya":
                continue
            if option in _TAKES_VALUE and not eq:
                pending = option
                continue
            inline = True
        if option == "--context":
            context = value
        elif option in _TAKES_VALUE:
            value = _rule(value) if option == "--ignore" else value
            out += [f"{option}={value}"] if inline else [option, value]
        else:
            out.append(option)
    if pending is not None:
        out.append(pending)  # its value is missing: text-lint's parser reports that
    profile = _PROFILES.get(context, context)
    return ["check", "--profile", profile, "--fail-on", "suggestion", *out, *argv[len(options):]]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print(DEPRECATION, file=sys.stderr)
    translated = translate(argv)
    if translated == ["--version"]:
        print(f"slop-check {__version__}")
        return 0
    return cli.main(translated)
