"""`slop-check install-skill`: advertise slop-check to agents on this machine.

Writes the one-line SessionStart blurb ``~/.agents/skills/.blurbs/slop-check.md``, the same
marker the sibling CLIs (task/tg/review) write. rig reads that file as "installed and
advertised"; without it every ``rig apply`` reruns install.sh. The full skill
(``~/.agents/skills/slop-check/SKILL.md``) ships from agent-tools, so it is not written here.
"""

import os
from pathlib import Path

TOOL = "slop-check"

# The SessionStart hook cats every .blurbs/*.md verbatim: keep this one bullet line.
BLURB = (
    "- `slop-check` — flag AI tells (stock phrases, flattery, hedging, agent status reports) "
    "in English and Russian prose before it is posted: `slop-check file.md`, "
    "`echo text | slop-check`, `--context issue|pr|tg|docs`, `--format json`, "
    "`--ignore RULE_ID`, `--laya` (advisory). Exit 0 clean, 1 findings, 2 error. "
    "Run it on PR/issue bodies and reports before posting.\n"
)


def install_skill():
    # expanduser, exactly as rig resolves the marker (USERPROFILE on native Windows, where a
    # Cygwin HOME would point elsewhere).
    home = Path(os.path.expanduser("~"))
    target = home / ".agents" / "skills" / ".blurbs" / f"{TOOL}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Compared as bytes: a damaged (non-UTF-8) blurb is simply rewritten.
    if target.is_file() and target.read_bytes() == BLURB.encode("utf-8"):
        print(f"{TOOL}: already current at {str(target)!r}")  # repr: HOME may hold escapes
        return 0
    # Atomic: rig treats the file's mere existence as "advertised", so a write cut short must
    # never leave an empty marker behind.
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(BLURB.encode("utf-8"))
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    print(f"{TOOL}: wrote {str(target)!r}")
    return 0
