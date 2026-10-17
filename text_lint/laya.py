"""Advisory slop probability from Laya, through task-cli's already-running classify server.

Only talks to an existing ``laya-*.sock`` under ``$XDG_STATE_HOME/task-cli/classify``
(task-cli ``classify_server``). It never starts the server and never loads a model here:
no socket means "unavailable", not an error. The score is advice and never changes the exit
code.

Protocol: one JSON line ``{"op": "predict", "message": ..., "questions": {...}}`` in, one
JSON line ``{"ok": true, "result": {"answers": {name: {"type": "noul", "noul": p}}}}`` out.
"""

import json
import os
import re
import socket
import stat
from pathlib import Path

QUESTION = (
    "Is this text AI-generated slop: buzzwords, stock phrases, hedging, flattery or vague "
    "claims with no concrete facts?"
)
# Probe on 2026-10-16: obvious English slop 0.32, Russian slop 0.60, plain technical English
# 0.0007, plain Russian 0.09. At or above this the score is listed as an advisory finding.
ADVISORY_THRESHOLD = 0.3
TIMEOUT_S = 30.0
_MAX_REPLY_BYTES = 4 * 1024 * 1024


def socket_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "task-cli" / "classify"


def _version_key(path):
    return [int(part) for part in re.findall(r"\d+", path.name)]


def find_socket():
    """The newest laya-*.sock in a private directory we own, or None."""
    directory = socket_dir()
    if not _trusted(directory):
        return None
    sockets = [p for p in directory.glob("laya-*.sock") if _is_socket(p)]
    return max(sockets, key=_version_key) if sockets else None


def _is_socket(path):
    # task-cli removes its socket on idle shutdown, possibly right after glob() saw it.
    try:
        return stat.S_ISSOCK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def _trusted(directory):
    """Whether only we could have put a socket into ``directory``: it is ours and private
    (task-cli's own rule), and no ancestor lets another user replace it: each is owned by us
    or root and is not writable by others, unless sticky (/tmp) or writable only through our
    own private group (task-cli creates ~/.local/state/task-cli as 0775)."""
    uid = os.getuid()
    try:
        st = os.lstat(directory)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
            return False
        for parent in Path(os.path.abspath(directory)).parents:
            st = os.stat(parent)
            if st.st_uid not in (uid, 0):
                return False
            if st.st_mode & stat.S_ISVTX:
                continue
            if st.st_mode & 0o002:
                return False
            if st.st_mode & 0o020 and not _private_group(st.st_gid):
                return False
    except OSError:
        return False
    return True


def _private_group(gid):
    """Our primary group with no other member, listed or primary (the user-private-group
    scheme): gr_mem omits users whose primary group it is, so passwd is checked too."""
    import grp  # POSIX only, and only reached on POSIX (see advise)
    import pwd

    if gid != os.getgid():
        return False
    try:
        members = grp.getgrgid(gid).gr_mem
    except KeyError:
        return False
    me = pwd.getpwuid(os.getuid()).pw_name
    if any(member != me for member in members):
        return False
    return all(user.pw_name == me for user in pwd.getpwall() if user.pw_gid == gid)


def advise(text):
    """{"status": "ok", "noul": p, "socket": path} or {"status": "unavailable", "reason": ...}."""
    if os.name != "posix":
        return {"status": "unavailable", "reason": "the Laya socket is a unix socket (POSIX only)"}
    path = find_socket()
    if path is None:
        return {"status": "unavailable", "reason": f"no laya-*.sock in {socket_dir()}"}
    request = {"op": "predict", "message": text,
               "questions": {"slop": {"type": "noul", "instructions": QUESTION}}}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(TIMEOUT_S)
            conn.connect(str(path))
            conn.sendall(json.dumps(request).encode() + b"\n")
            with conn.makefile("rb") as reader:
                reply = json.loads(reader.readline(_MAX_REPLY_BYTES + 1))
        if not reply.get("ok"):
            return {"status": "unavailable", "reason": f"{path}: server error {reply.get('error')}"}
        noul = float(reply["result"]["answers"]["slop"]["noul"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {"status": "unavailable", "reason": f"{path}: {type(exc).__name__}: {exc}"}
    if not 0.0 <= noul <= 1.0:  # also rejects NaN
        return {"status": "unavailable", "reason": f"{path}: probability out of range: {noul}"}
    return {"status": "ok", "noul": round(noul, 4), "socket": str(path)}
