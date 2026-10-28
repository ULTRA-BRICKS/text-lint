"""Wire contracts through a real Unix socket and the public provider API."""
import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from text_lint import TextLintError, check
from text_lint.engines import laya


@pytest.fixture(autouse=True)
def laya_server(monkeypatch, tmp_path):
    """Own bounded server teardown; override the shared autouse server locally."""
    requests = []
    reply = [b'']
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="w-") as directory:
        path = Path(directory) / "s"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen()
        server.settimeout(0.05)

        def serve():
            while not stop.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with conn:
                    conn.settimeout(1)
                    with conn.makefile("rb") as reader:
                        requests.append(json.loads(reader.readline()))
                    conn.sendall(reply[0])

        thread = threading.Thread(target=serve)
        thread.start()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        shutil.copy(Path(__file__).parent / "fakes/task", bin_dir / "task")
        (bin_dir / "task").chmod(0o755)
        monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
        monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(path))
        monkeypatch.setenv("FAKE_TASK_LOG", str(tmp_path / "task.log"))
        try:
            yield reply, requests
        finally:
            stop.set()
            server.close()
            thread.join(2)
            assert not thread.is_alive()
            assert not (tmp_path / "task.log").exists()


def wire(answer):
    return json.dumps({"ok": True, "result": {"answers": {"slop": answer}}}).encode() + b"\n"


def public_check():
    return check("Hello", profile="chat", laya_mode="required")


@pytest.mark.parametrize("answer", [
    {"type": "noul", "noul": 0.0}, {"type": "noul", "noul": 1},
    {"noul": 0.0}, {"noul": 0.123456},
    {"type": "noul", "noul": 0.0, "confidence": 1.0,
     "answer_confidence": 0.9, "action": "act"},
])
def test_supported_answers(laya_server, answer):
    reply, requests = laya_server
    reply[0] = wire(answer)
    report = public_check()
    assert report["engines"]["laya"]["status"] == "ok"
    assert report["engines"]["laya"]["answers"]["slop"] == round(answer["noul"], 4)
    assert report["verdict"] == "pass"
    assert requests[0]["questions"] == {
        "slop": {"type": "noul", "instructions": laya.QUESTIONS["slop"]}}


@pytest.mark.parametrize("answer", [
    {"type": t, "noul": 0.0} for t in ("not-noul", "choice", "score", "", None, True, [], {})
] + [None, [], "noul", 0, {}, {"type": "noul"}] + [
    {"type": "noul", "noul": p} for p in (None, True, False, "0", [], {}, -0.01, 1.01,
                                            float("nan"), float("inf"), -float("inf"))
] + [{"type": "noul", "noul": 0.0, "choice": "yes"},
     {"noul": 0.0, "score": 0.0}])
def test_malformed_answers_refuse_public_check(laya_server, answer):
    laya_server[0][0] = wire(answer)
    with pytest.raises(TextLintError, match="Laya"):
        public_check()


@pytest.mark.parametrize("payload", [
    '{"type":"not-noul","type":"noul","noul":0}',
    '{"type":"noul","noul":1,"noul":0}',
    '{"type":"noul","noul":0,"noul":0}',
])
def test_duplicate_answer_fields(laya_server, payload):
    laya_server[0][0] = ('{"ok":true,"result":{"answers":{"slop":' + payload + '}}}\n').encode()
    with pytest.raises(TextLintError, match="Laya"):
        public_check()


@pytest.mark.parametrize("payload", [
    '{"ok":true,"result":{"answers":{"slop":{"noul":1},"slop":{"noul":0}}}}',
    '{"ok":true,"result":{"answers":[]}}',
    '{"ok":true,"result":[]}',
    '{"ok":true,"result":{"answers":{}}}',
    '{"ok":false,"error":"failed"}',
    '[]',
])
def test_bad_envelopes(laya_server, payload):
    laya_server[0][0] = payload.encode() + b"\n"
    with pytest.raises(TextLintError, match="Laya"):
        public_check()


def test_malformed_listener_is_not_absent(laya_server):
    laya_server[0][0] = wire({"type": "not-noul", "noul": 0})
    with pytest.raises(laya.LayaUnavailable) as error:
        laya.ask("Hello", ["slop"], start=False)
    assert not isinstance(error.value, laya.LayaNotListening)
