"""Laya answers through task-cli's local classify server (a Unix socket; nothing goes to the
network). Laya is mandatory: every profile asks it, in one request per run.

The socket is ``TEXT_LINT_LAYA_SOCKET`` when set (tests, non-default sockets; it is never
started), else the ``laya-*.sock`` with the highest version under
``$XDG_STATE_HOME/task-cli/classify`` (task-cli ``classify_server``) that accepts a
connection. When none does, ``task classify-server start`` (task-cli 0.23 or later) starts
the server once, within the profile's ``start_wait_ms``, and prints the socket path it
started or reused; one request then goes to that socket. Any failure raises LayaUnavailable,
which ends the run with exit 2 naming Laya (check.py decides the two exceptions: an
automated message and `--laya-mode ci` on a CI runner). This module never loads a model.

Protocol: one JSON line ``{"op": "predict", "message": ..., "questions": {...}}`` in, one
JSON line ``{"ok": true, "result": {"answers": {name: {"type": "noul", "noul": p}}}}`` out.
"""

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from ..profiles import ConfigError
from . import EngineError

# The questions a profile may ask (engines.laya.options.questions), sent as `noul` questions.
QUESTIONS = {
    # slop-check 0.1's question.
    "slop": (
        "Is this text AI-generated slop: buzzwords, stock phrases, hedging, flattery or vague "
        "claims with no concrete facts?"
    ),
    # tg-cli LAYA_QUESTIONS.asks_reader (features/cli/laya-socket.ts:27-32).
    "asks_reader": "Does the message ask the reader to decide, choose, approve, or answer an open question?",
    "first_person": "Does this read as a person writing in the first person, not as a bot or report?",
}
# Probe on 2026-10-16: obvious English slop 0.32, Russian slop 0.60, plain technical English
# 0.0007, plain Russian 0.09. At or above this the slop score is listed as an advisory finding.
ADVISORY_THRESHOLD = 0.3
SOCKET_ENV = "TEXT_LINT_LAYA_SOCKET"
# Overrides a profile's engines.laya.options.timeout_ms (tests use it).
TIMEOUT_ENV = "TEXT_LINT_LAYA_TIMEOUT_MS"
START_COMMAND = ("classify-server", "start")
_MAX_REPLY_BYTES = 4 * 1024 * 1024


class LayaUnavailable(EngineError):
    """Laya could not answer: no server could be reached or started, or the request failed."""

    def __init__(self, problem):
        super().__init__("Laya", problem)


class LayaNotListening(LayaUnavailable):
    """No local listener answered; CI may report this as not run."""


def socket_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "task-cli" / "classify"


def _version_key(path):
    return [int(part) for part in re.findall(r"\d+", path.name)]


def candidate_sockets():
    """The laya-*.sock files in task-cli's socket directory, highest version first; none when
    the directory is not private to this user."""
    directory = socket_dir()
    if not _trusted(directory):
        return []
    return sorted((p for p in directory.glob("laya-*.sock") if _is_socket(p)), key=_version_key, reverse=True)


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


def request_timeout_s(timeout_ms):
    """The request timeout in seconds: ``TEXT_LINT_LAYA_TIMEOUT_MS`` when set, else the
    profile's ``timeout_ms``."""
    value = os.environ.get(TIMEOUT_ENV)
    if value is None:
        return timeout_ms / 1000
    try:
        ms = int(value)
    except ValueError:
        ms = 0
    if ms <= 0:
        raise ConfigError(f"{TIMEOUT_ENV}={value!r} is not a positive number of milliseconds")
    return ms / 1000


def ask(text, questions, *, timeout_s=1.5, start_wait_s=60.0, start=True):
    """Laya's answers to ``questions`` (QUESTIONS keys) about ``text``, in one request:
    {"answers": {question: p, or None when the reply leaves it out}, "socket": path}.
    ``start=False`` (CI runners) never runs the start command. LayaUnavailable otherwise."""
    if os.name != "posix":
        raise LayaUnavailable("the Laya socket is a unix socket (POSIX only)")
    started = time.monotonic()
    deadline = started + timeout_s
    cold_deadline = started + start_wait_s
    explicit = os.environ.get(SOCKET_ENV)
    if explicit:
        path = Path(explicit)
        # The text goes to this socket: the same private-directory rule as the default one.
        if not _trusted(path.parent):
            raise LayaUnavailable(f"{SOCKET_ENV}={explicit}: its directory is not private to this user")
        conn = _connect(path, _remaining(deadline))
        if conn is None:
            raise LayaNotListening(f"{SOCKET_ENV}={explicit}: no server answers (a socket named there is never started)")
    else:
        path, conn = _connect_highest(deadline)
        if conn is None:
            if not start:
                raise LayaNotListening(f"no laya-*.sock in {socket_dir()} accepts a connection")
            # Discovery and startup share the cold-start allowance. Once ready, the
            # single predict request gets its own warm budget, including its connect.
            path = _start(_remaining(cold_deadline, "cold start"))
            _remaining(cold_deadline, "cold start")
            deadline = time.monotonic() + timeout_s
            conn = _connect(path, _remaining(deadline))
            if conn is None:
                raise LayaUnavailable(f"{path}: the started server does not accept a connection")
    request = {"op": "predict", "message": text,
               "questions": {q: {"type": "noul", "instructions": QUESTIONS[q]} for q in questions}}
    with conn:
        return {"answers": _exchange(conn, path, request, questions, deadline=deadline), "socket": str(path)}


def advise(text, *, question, timeout_s=1.5, start_wait_s=60.0):
    """Laya's answer to one ``question`` about ``text``: {"noul": p or None, "socket": path}."""
    result = ask(text, [question], timeout_s=timeout_s, start_wait_s=start_wait_s)
    return {"noul": result["answers"][question], "socket": result["socket"]}


def _remaining(deadline, phase="warm request"):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LayaUnavailable(f"{phase} deadline exceeded")
    return remaining


def _connect_highest(deadline):
    """Try highest versions first, sharing one discovery/connect deadline."""
    for path in candidate_sockets():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        conn = _connect(path, remaining)
        if conn is not None:
            return path, conn
    return None, None


def _connect(path, timeout_s):
    """A connected socket to ``path``, or None when nothing listens there."""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout_s)
    try:
        conn.connect(str(path))
    except OSError:
        conn.close()
        return None
    return conn


def _start(wait_s):
    """The socket path `task classify-server start` prints once the server it started (or
    found running) accepts connections. The command gets ``wait_s`` seconds; its output goes
    to files, so a server it leaves running cannot hold this process on a pipe."""
    task = shutil.which("task")
    if task is None:
        raise LayaUnavailable("no socket answers and task-cli's `task` is not on PATH to start the classify server")
    command = f"`task {' '.join(START_COMMAND)}`"
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen([task, *START_COMMAND], stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        except OSError as exc:
            raise LayaUnavailable(f"could not run {command}: {exc}") from None
        try:
            code = proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise LayaUnavailable(f"{command} did not finish within {wait_s:g} s") from None
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace").strip()
    if code != 0:
        raise LayaUnavailable(f"{command} exited {code}: {stderr.splitlines()[-1] if stderr else 'no message'}")
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    path = Path(lines[-1]) if lines else None
    if path is None or not path.is_absolute() or not _is_socket(path):
        raise LayaUnavailable(f"{command} printed no socket path: {stdout.strip()[:200]!r}")
    if not _trusted(path.parent):
        raise LayaUnavailable(f"{command} printed {path}: its directory is not private to this user")
    return path


def _exchange(conn, path, request, questions, *, deadline):
    try:
        # A stream that keeps sending a byte before each socket timeout still gets only
        # one warm-request budget, including the send and every reply chunk.
        conn.settimeout(_remaining(deadline))
        conn.sendall(json.dumps(request).encode() + b"\n")
        buffer = bytearray()
        while b"\n" not in buffer:
            conn.settimeout(_remaining(deadline))
            chunk = conn.recv(min(4096, _MAX_REPLY_BYTES + 1 - len(buffer)))
            if not chunk:
                raise ValueError("reply ended before the JSON line delimiter")
            buffer.extend(chunk)
            if len(buffer) > _MAX_REPLY_BYTES:
                raise ValueError("reply exceeds maximum size")
        reply = json.loads(buffer.partition(b"\n")[0])
        if not isinstance(reply, dict):
            raise ValueError("reply must be a JSON object")  # noqa: TRY004 - schema validation uses ValueError
        if reply.get("ok") is not True:
            raise LayaUnavailable(f"{path}: server error {reply.get('error')}")
        result = reply["result"]
        if not isinstance(result, dict):
            raise ValueError("result must be a JSON object")  # noqa: TRY004 - schema validation uses ValueError
        answers = result["answers"]
        if not isinstance(answers, dict):
            raise ValueError("answers must be a JSON object")  # noqa: TRY004 - schema validation uses ValueError
        found = {q: answers[q] for q in questions if q in answers}
        values = {q: found[q]["noul"] if q in found else None for q in questions}
        if any(p is not None and type(p) not in (int, float) for p in values.values()):
            raise ValueError("probability must be a JSON number")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LayaUnavailable(f"{path}: {type(exc).__name__}: {exc}") from None
    for q, p in values.items():
        if p is not None and not 0.0 <= p <= 1.0:  # also rejects NaN
            raise LayaUnavailable(f"{path}: probability out of range for {q}: {p}")
    return {q: None if p is None else round(p, 4) for q, p in values.items()}
