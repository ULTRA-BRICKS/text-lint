import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

# Hermetic: import the checkout's text_lint, never an installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKES = Path(__file__).resolve().parent / "fakes"
EMPTY_DICT = Path(__file__).resolve().parent / "fixtures" / "empty-DICT.json"


@pytest.fixture(autouse=True)
def no_owner_dictionary(monkeypatch, tmp_path):
    """Every test reads an empty dictionary unless it names one: the owner's
    ~/.claude/DICT.json (or a TG_DICT_PATH in the environment) must not change results."""
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("TEXT_LINT_DICT", str(EMPTY_DICT))
    monkeypatch.delenv("TG_DICT_PATH", raising=False)


@pytest.fixture
def fake_engines(monkeypatch, tmp_path):
    """Fake vale and aiw-ru (tests/fakes) logging their calls under ``tmp_path``, which is
    returned. Not autouse: tests/test_integration.py runs the real engines."""
    monkeypatch.setenv("TEXT_LINT_VALE", str(FAKES / "vale"))
    monkeypatch.setenv("TEXT_LINT_AIWRU", str(FAKES / "aiw-ru"))
    monkeypatch.setenv("FAKE_VALE_LOG", str(tmp_path / "vale.log"))
    monkeypatch.setenv("FAKE_AIWRU_LOG", str(tmp_path / "aiwru.log"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


class FakeLaya:
    """Unix socket server speaking task-cli's classify_server line protocol: one JSON request
    line in, one reply line out. Every question in a request is answered with ``answers[name]``,
    else ``noul``; ``raw_reply`` (bytes) replaces the answer, ``hang`` reads the request and
    never answers. ``requests`` records every request."""

    def __init__(self, path, noul=0.1, raw_reply=None, hang=False):
        self.path = Path(path)
        self.requests = []
        self.noul = noul
        self.answers = {}
        self.raw_reply = raw_reply
        self.chunk_delay = None
        self.hang = threading.Event() if hang else None
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.path))
        self.sock.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rb") as reader:
                try:
                    req = json.loads(reader.readline())
                except ValueError:
                    continue
                self.requests.append(req)
                if self.hang is not None:
                    self.hang.wait(10)
                    continue
                if self.raw_reply is not None:
                    try:
                        if self.chunk_delay is None:
                            conn.sendall(self.raw_reply)
                        else:
                            for byte in self.raw_reply:
                                time.sleep(self.chunk_delay)
                                conn.sendall(bytes([byte]))
                    except OSError:
                        pass  # the client may enforce its deadline before all bytes arrive
                    continue
                answers = {name: {"type": "noul", "noul": self.answers.get(name, self.noul)}
                           for name in req.get("questions", {})}
                conn.sendall(json.dumps({"ok": True, "result": {"answers": answers}}).encode() + b"\n")

    def close(self):
        if self.hang is not None:
            self.hang.set()
        self.sock.close()


@pytest.fixture(autouse=True)
def laya_server(monkeypatch, tmp_path):
    """Every test talks to a fake Laya through TEXT_LINT_LAYA_SOCKET and finds a fake `task`
    first on PATH (its `classify-server start` prints the fake socket path and logs the call to
    ``tmp_path / "task.log"``), so no test can reach the real server or the real task-cli.
    Tests that need other answers set ``laya_server.noul`` / ``.answers``."""
    # AF_UNIX paths are capped at ~108 bytes, so not pytest's long tmp_path.
    directory = tempfile.mkdtemp(prefix="tl")
    os.chmod(directory, 0o700)
    server = FakeLaya(Path(directory) / "laya-fake.sock")
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    shutil.copy(FAKES / "task", bin_dir / "task")
    (bin_dir / "task").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_TASK_SOCKET", str(server.path))
    monkeypatch.setenv("FAKE_TASK_LOG", str(tmp_path / "task.log"))
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(server.path))
    yield server
    server.close()
    shutil.rmtree(directory, ignore_errors=True)
