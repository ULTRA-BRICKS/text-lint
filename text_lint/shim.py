"""`slop-check`, the 0.1 command name, kept while callers move to `text-lint`.

It prints one deprecation line to stderr and runs `text-lint check` with the 0.1 arguments,
mapping the renamed rule pack in `--ignore` (`SlopCheck.X` → `TextLint.X`).
`slop-check --version` answers `slop-check <version>`, as 0.1 did.
`slop-check install-skill`, which older install.sh copies call, runs `text-lint install-skill`.
The shim goes once nothing calls `slop-check` (spec section 4.1).
"""

import sys

from . import __version__, cli

DEPRECATION = "slop-check is deprecated; use text-lint check"
_OLD_STYLE, _NEW_STYLE = "SlopCheck", "TextLint"


def _rule(value):
    if value == _OLD_STYLE or value.startswith(_OLD_STYLE + "."):
        return _NEW_STYLE + value[len(_OLD_STYLE):]
    return value


def _abbreviates(arg, option):
    """``arg`` names ``option`` as 0.1's argparse read it: any prefix down to `--i` / `--v`,
    which no other 0.1 option starts with."""
    return len(arg) >= 3 and option.startswith(arg)


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
    out = ["check"]
    after_ignore = False
    for arg in options:
        name, eq, value = arg.partition("=")
        if after_ignore:
            arg = _rule(arg)
            after_ignore = False
        elif _abbreviates(name, "--ignore"):
            if eq:
                arg = f"{name}={_rule(value)}"
            else:
                after_ignore = True
        out.append(arg)
    return out + argv[len(options):]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print(DEPRECATION, file=sys.stderr)
    translated = translate(argv)
    if translated == ["--version"]:
        print(f"slop-check {__version__}")
        return 0
    return cli.main(translated)
