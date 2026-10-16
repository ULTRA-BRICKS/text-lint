"""--laya: advisory score from an already-running task-cli classify server (faked here)."""

import io
import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from slop_cli import laya
from slop_cli.cli import main

FAKES = Path(__file__).resolve().parent / "fakes"


@pytest.fixture
def state_home(monkeypatch):
    # AF_UNIX paths are capped at ~108 bytes, so not pytest's long tmp_path.
    home = tempfile.mkdtemp(prefix="slt")
    monkeypatch.setenv("XDG_STATE_HOME", home)
    monkeypatch.setenv("SLOP_CHECK_VALE", str(FAKES / "vale"))
    monkeypatch.setenv("SLOP_CHECK_AIWRU", str(FAKES / "aiw-ru"))
    yield Path(home)
    shutil.rmtree(home, ignore_errors=True)


class FakeLaya:
    """One-request-at-a-time unix socket server speaking the classify_server line protocol."""

    def __init__(self, path, noul=None, raw_reply=None, hang=False):
        self.requests = []
        self.noul = noul
        self.raw_reply = raw_reply  # bytes sent instead of a well-formed answer
        self.hang = threading.Event() if hang else None  # read the request, never answer
        # Every level private, whatever the umask: slop-check refuses a socket that another
        # user could have planted or swapped in.
        for directory in (path.parent.parent, path.parent):
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rb") as reader:
                req = json.loads(reader.readline())
                self.requests.append(req)
                if self.hang is not None:
                    self.hang.wait(10)
                    continue
                if self.raw_reply is not None:
                    conn.sendall(self.raw_reply)
                    continue
                answers = {name: {"type": "noul", "noul": self.noul} for name in req["questions"]}
                conn.sendall(json.dumps({"ok": True, "result": {"answers": answers}}).encode() + b"\n")

    def close(self):
        if self.hang is not None:
            self.hang.set()
        self.sock.close()


def run(argv, capsys, monkeypatch, stdin):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(argv)
    return code, capsys.readouterr().out


def test_no_socket_reports_unavailable_without_error(state_home, capsys, monkeypatch):
    code, out = run(["--laya", "--format", "json"], capsys, monkeypatch, "Plain text.\n")
    data = json.loads(out)
    assert code == 0
    assert data["engines"]["laya"]["status"] == "unavailable"
    code, out = run(["--laya"], capsys, monkeypatch, "Plain text.\n")
    assert code == 0
    assert "laya: unavailable" in out


def test_laya_is_off_unless_asked(state_home, capsys, monkeypatch):
    _, out = run(["--format", "json"], capsys, monkeypatch, "Plain text.\n")
    assert json.loads(out)["engines"]["laya"]["status"] == "off"


def test_running_socket_gives_advisory_score_that_never_changes_exit_code(state_home, capsys, monkeypatch):
    sock_path = state_home / "task-cli" / "classify" / "laya-0.22.0.sock"
    server = FakeLaya(sock_path, noul=0.91)
    try:
        code, out = run(["--laya", "--format", "json"], capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    data = json.loads(out)
    assert code == 0  # a high Laya score alone is advisory
    assert data["engines"]["laya"] == {"status": "ok", "noul": 0.91, "socket": str(sock_path)}
    advisory = [f for f in data["findings"] if f["engine"] == "laya"]
    assert advisory and advisory[0]["severity"] == "suggestion"
    (req,) = server.requests
    assert req["op"] == "predict"
    assert req["message"] == "Plain text.\n"
    (question,) = req["questions"].values()
    assert question["type"] == "noul"
    assert question["instructions"] == laya.QUESTION


@pytest.mark.parametrize("dir_to_open, mode", [
    ("task-cli/classify", 0o777),  # world-writable socket directory
    ("task-cli/classify", 0o770),  # group members could plant a socket
    ("task-cli", 0o777),           # anyone could swap the private directory below it
    (".", 0o777),                  # ... or the whole state directory
])
def test_socket_dir_others_can_write_is_not_trusted(state_home, capsys, monkeypatch, dir_to_open, mode):
    server = FakeLaya(state_home / "task-cli" / "classify" / "laya-0.22.0.sock", noul=0.5)
    os.chmod(state_home / dir_to_open, mode)
    try:
        _, out = run(["--laya", "--format", "json"], capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    assert json.loads(out)["engines"]["laya"]["status"] == "unavailable"
    assert server.requests == []


@pytest.mark.parametrize("reply", [
    b'{"ok": true}\n',
    b'{"ok": false, "error": "ValueError"}\n',
    b'{"ok": true, "result": {"answers": {"other": {"noul": 0.5}}}}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": "high"}}}}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": NaN}}}}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": 7}}}}\n',
    b"not json\n",
    b"[]\n",
])
def test_malformed_reply_is_unavailable(state_home, capsys, monkeypatch, reply):
    server = FakeLaya(state_home / "task-cli" / "classify" / "laya-0.22.0.sock", raw_reply=reply)
    try:
        code, out = run(["--laya", "--format", "json"], capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    assert code == 0
    assert json.loads(out)["engines"]["laya"]["status"] == "unavailable"


def test_silent_server_times_out_as_unavailable(state_home, capsys, monkeypatch):
    monkeypatch.setattr(laya, "TIMEOUT_S", 0.5)
    server = FakeLaya(state_home / "task-cli" / "classify" / "laya-0.22.0.sock", hang=True)
    try:
        code, out = run(["--laya", "--format", "json"], capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    assert code == 0
    assert json.loads(out)["engines"]["laya"]["status"] == "unavailable"


def test_socket_removed_between_glob_and_stat_is_unavailable(state_home, capsys, monkeypatch):
    directory = laya.socket_dir()
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory.parent, 0o700)
    monkeypatch.setattr(Path, "glob", lambda self, pattern: iter([self / "laya-0.22.0.sock"]))
    code, out = run(["--laya", "--format", "json"], capsys, monkeypatch, "Plain text.\n")
    assert code == 0
    assert json.loads(out)["engines"]["laya"]["status"] == "unavailable"


def test_group_shared_as_someone_elses_primary_group_is_not_private(monkeypatch):
    import grp
    import pwd

    me = pwd.getpwuid(os.getuid())
    other = pwd.struct_passwd(("intruder", "x", me.pw_uid + 1, os.getgid(), "", "/", "/bin/sh"))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(("g", "x", gid, [])))
    monkeypatch.setattr(pwd, "getpwall", lambda: [me, other])
    assert laya._private_group(os.getgid()) is False
    monkeypatch.setattr(pwd, "getpwall", lambda: [me])
    assert laya._private_group(os.getgid()) is (me.pw_gid == os.getgid())
