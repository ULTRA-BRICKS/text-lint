"""`text-lint install-skill`: advertise text-lint to agents on this machine.

Writes the one-line SessionStart blurb ``~/.agents/skills/.blurbs/text-lint.md``, the same
marker the sibling CLIs (task/tg/review) write. rig reads ``.blurbs/<tools.items name>.md`` as
"installed and advertised"; without it every ``rig apply`` reruns install.sh. The full skill
(``~/.agents/skills/slop-check/SKILL.md``) ships from agent-tools, so it is not written here.
slop-check 0.1 wrote ``.blurbs/slop-check.md``; that file is removed, so the SessionStart hook
does not advertise the tool twice.
"""

import os
from pathlib import Path

TOOL = "text-lint"
OLD_TOOL = "slop-check"

# The SessionStart hook cats every .blurbs/*.md verbatim: keep this one bullet line.
BLURB = (
    "- `text-lint`: flag AI tells (stock phrases, flattery, hedging, agent status reports) "
    "in English and Russian prose before it is posted: `text-lint check file.md`, "
    "`echo text | text-lint check`, `--profile prose|issue|pr|commit|...`, `--format json`, "
    "`--ignore RULE_ID`. Exit 0 pass/warn, 1 block, 2 error. "
    "Run it on PR/issue bodies and reports before posting (`slop-check` is its deprecated "
    "old name).\n"
)


def install_skill():
    # expanduser, exactly as rig resolves the marker (USERPROFILE on native Windows, where a
    # Cygwin HOME would point elsewhere).
    home = Path(os.path.expanduser("~"))
    blurbs = home / ".agents" / "skills" / ".blurbs"
    target = blurbs / f"{TOOL}.md"
    blurbs.mkdir(parents=True, exist_ok=True)
    # Compared as bytes: a damaged (non-UTF-8) blurb is simply rewritten.
    if target.is_file() and target.read_bytes() == BLURB.encode("utf-8"):
        print(f"{TOOL}: already current at {str(target)!r}")  # repr: HOME may hold escapes
    else:
        # Atomic: rig treats the file's mere existence as "advertised", so a write cut short
        # must never leave an empty marker behind.
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(BLURB.encode("utf-8"))
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        print(f"{TOOL}: wrote {str(target)!r}")
    (blurbs / f"{OLD_TOOL}.md").unlink(missing_ok=True)
    return 0
