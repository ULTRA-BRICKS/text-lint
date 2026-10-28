"""Laya through task-cli's classify server (faked: tests/conftest.py FakeLaya and
tests/fakes/task). Every profile requires it: a refused or absent socket starts the server once
through `task classify-server start`; still no answer is exit 2 naming Laya, except for an
automated message (reported as unreachable) and a CI runner with `--laya-mode ci`."""

import io
import json
import os
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest
from conftest import FakeLaya
from messages import input_doc

from text_lint import profiles
from text_lint.cli import main
from text_lint.engines import laya


@pytest.fixture
def state_home(fake_engines, monkeypatch):
    """No TEXT_LINT_LAYA_SOCKET: the socket is found under XDG_STATE_HOME as in real use.
    AF_UNIX paths are capped at ~108 bytes, so not pytest's long tmp_path."""
    monkeypatch.delenv("TEXT_LINT_LAYA_SOCKET")
    home = tempfile.mkdtemp(prefix="slt")
    monkeypatch.setenv("XDG_STATE_HOME", home)
    yield Path(home)
    shutil.rmtree(home, ignore_errors=True)


def socket_path(state_home, version="0.22.0"):
    """task-cli's socket path under ``state_home``, every level private, whatever the umask:
    text-lint refuses a socket another user could have planted."""
    path = state_home / "task-cli" / "classify" / f"laya-{version}.sock"
    for directory in (path.parent.parent, path.parent):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    return path


def serve(state_home, version="0.22.0", **kwargs):
    return FakeLaya(socket_path(state_home, version), **kwargs)


def dead_socket(path):
    """A socket file nobody listens on: connecting is refused."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.close()
    return path


def run(argv, capsys, monkeypatch, stdin):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["check", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def task_calls(tmp_path):
    log = tmp_path / "task.log"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


ISSUE_JSON = ["--profile", "issue", "--format", "json"]


def test_laya_warm_call(state_home, capsys, monkeypatch, tmp_path):
    server = serve(state_home, noul=0.91)
    try:
        code, out, _ = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    data = json.loads(out)
    assert code == 0  # a high Laya score alone is advisory
    assert data["engines"]["laya"] == {"status": "ok", "answers": {"slop": 0.91}, "socket": str(server.path)}
    advisory = [f for f in data["findings"] if f["engine"] == "laya"]
    assert advisory and advisory[0]["severity"] == "suggestion"
    assert data["verdict"] == "pass"
    (req,) = server.requests
    assert req["op"] == "predict"
    assert req["message"] == "Plain text.\n"
    assert req["questions"] == {"slop": {"type": "noul", "instructions": laya.QUESTIONS["slop"]}}
    assert task_calls(tmp_path) == []


def test_laya_cold_start(state_home, capsys, monkeypatch, tmp_path, laya_server, cold_task_child):
    path = socket_path(state_home, "0.99.0")
    request_log = tmp_path / "cold-request.json"
    assert not path.exists()
    monkeypatch.setenv("FAKE_TASK_SOCKET", str(path))
    monkeypatch.setenv("FAKE_TASK_CREATE_SOCKET", "1")
    monkeypatch.setenv("FAKE_TASK_REQUEST_LOG", str(request_log))
    code, out, _ = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert code == 0
    assert json.loads(out)["engines"]["laya"]["socket"] == str(path)
    assert task_calls(tmp_path) == [["classify-server", "start"]]
    assert json.loads(request_log.read_text())["message"] == "Plain text.\n"
    assert laya_server.requests == []


def test_highest_version_that_accepts_wins(state_home, capsys, monkeypatch, tmp_path):
    old = serve(state_home, "0.22.0")
    dead_socket(socket_path(state_home, "0.23.0"))
    try:
        code, out, _ = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    finally:
        old.close()
    assert code == 0
    assert json.loads(out)["engines"]["laya"]["socket"] == str(old.path)
    assert len(old.requests) == 1 and task_calls(tmp_path) == []


def test_laya_start_fails(state_home, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_TASK_FAIL", "1")
    code, out, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert code == 2 and out == ""
    assert "Laya" in err and "classify-server failed to start" in err
    assert task_calls(tmp_path) == [["classify-server", "start"]]


def test_laya_start_without_task_cli(state_home, capsys, monkeypatch, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code, out, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert code == 2 and out == ""
    assert "Laya" in err and "task" in err


def test_laya_start_that_hangs_is_bounded(state_home, capsys, monkeypatch, tmp_path):
    shrunk = tmp_path / "profiles"
    shutil.copytree(profiles.PROFILE_DIR, shrunk)
    path = shrunk / "issue.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("start_wait_ms: 60000", "start_wait_ms: 500"),
                    encoding="utf-8")
    monkeypatch.setattr(profiles, "PROFILE_DIR", shrunk)
    monkeypatch.setenv("FAKE_TASK_HANG", "10")
    started = time.monotonic()
    code, _, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert time.monotonic() - started < 5
    assert code == 2 and "Laya" in err and "did not finish within" in err


def test_laya_mode_ci_outside_ci(capsys, monkeypatch, fake_engines):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("Plain text.\n"))
    with pytest.raises(SystemExit) as exc:
        main(["check", "--profile", "pr", "--laya-mode", "ci"])
    assert exc.value.code == 2 and "CI=true" in capsys.readouterr().err


def test_laya_mode_ci_on_a_runner_without_laya(state_home, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CI", "true")
    code, out, _ = run(["--profile", "pr", "--laya-mode", "ci"], capsys, monkeypatch, "Plain text.\n")
    assert code == 0
    assert "laya: not run (ci runner, no local model)" in out
    assert task_calls(tmp_path) == []


def test_laya_mode_ci_with_a_socket_asks_laya(state_home, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CI", "true")
    server = serve(state_home, noul=0.2)
    try:
        code, out, _ = run(["--profile", "pr", "--laya-mode", "ci", "--format", "json"], capsys, monkeypatch,
                           "Plain text.\n")
    finally:
        server.close()
    assert code == 0
    assert json.loads(out)["engines"]["laya"]["answers"] == {"slop": 0.2}


def test_explicit_socket_never_starts_the_server(capsys, monkeypatch, fake_engines, laya_server, tmp_path):
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(laya_server.path.parent / "laya-dead.sock"))
    code, out, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert code == 2 and out == "" and "Laya" in err
    assert task_calls(tmp_path) == []


def test_unreachable_laya_for_an_automated_message(capsys, monkeypatch, fake_engines, laya_server, tmp_path):
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(laya_server.path.parent / "laya-dead.sock"))
    argv = ["--profile", "issue", "--input-json"]
    code, out, _ = run(argv, capsys, monkeypatch, input_doc([("body", "Plain text.", "plain")], automated=True))
    assert code == 0
    assert "laya: unreachable (" in out
    code, out, _ = run([*argv, "--format", "json"], capsys, monkeypatch,
                       input_doc([("body", "We utilize caches.", "plain")], automated=True))
    data = json.loads(out)
    assert data["engines"]["laya"]["status"] == "unreachable"
    # The other engines still decide the verdict.
    assert code == 1 and data["verdict"] == "block"
    code, out, err = run(argv, capsys, monkeypatch, input_doc([("body", "Plain text.", "plain")]))
    assert code == 2 and "Laya" in err
    assert task_calls(tmp_path) == []


def test_one_laya_request_per_run(capsys, monkeypatch, fake_engines, laya_server):
    doc = input_doc([(f"f{i}", f"Field number {i}.", "plain") for i in range(10)])
    code, _, _ = run(["--profile", "prose", "--input-json"], capsys, monkeypatch, doc)
    assert code == 0
    (req,) = laya_server.requests
    assert req["message"] == "\n\n".join(f"Field number {i}." for i in range(10))


def test_laya_question_keys(laya_server):
    advice = laya.advise("Merge it?", question="asks_reader")
    assert advice == {"noul": 0.1, "socket": str(laya_server.path)}
    advice = laya.advise("Merge it?", question="slop")
    assert advice["noul"] == 0.1
    asks, slop = laya_server.requests
    assert asks["questions"] == {"asks_reader": {"type": "noul", "instructions": laya.QUESTIONS["asks_reader"]}}
    assert slop["questions"] == {"slop": {"type": "noul", "instructions": laya.QUESTIONS["slop"]}}
    laya_server.raw_reply = b'{"ok": true, "result": {"answers": {"slop": {"type": "noul", "noul": 0.4}}}}\n'
    assert laya.advise("Merge it?", question="asks_reader")["noul"] is None


def test_laya_timeout_respected(capsys, monkeypatch, fake_engines):
    directory = tempfile.mkdtemp(prefix="tl")
    os.chmod(directory, 0o700)
    server = FakeLaya(Path(directory) / "laya-slow.sock", hang=True)
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(server.path))
    try:
        started = time.monotonic()
        with pytest.raises(laya.LayaUnavailable):
            laya.advise("text", question="slop", timeout_s=1.5)
        assert time.monotonic() - started < 2
        code, out, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
        shutil.rmtree(directory, ignore_errors=True)
    assert code == 2 and out == "" and "Laya" in err


def test_explicit_socket_in_a_shared_directory_is_not_trusted(capsys, monkeypatch, fake_engines, laya_server):
    os.chmod(laya_server.path.parent, 0o777)
    code, _, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert code == 2 and "not private" in err
    assert laya_server.requests == []


def test_long_text_is_cut_to_max_chars(capsys, monkeypatch, fake_engines, laya_server):
    run(ISSUE_JSON, capsys, monkeypatch, "word " * 1000)
    (req,) = laya_server.requests
    assert len(req["message"]) == 2000


@pytest.mark.parametrize("dir_to_open, mode", [
    ("task-cli/classify", 0o777),  # world-writable socket directory
    ("task-cli/classify", 0o770),  # group members could plant a socket
    ("task-cli", 0o777),           # anyone could swap the private directory below it
    (".", 0o777),                  # ... or the whole state directory
])
def test_socket_dir_others_can_write_is_not_trusted(state_home, capsys, monkeypatch, tmp_path, laya_server,
                                                     dir_to_open, mode):
    planted = serve(state_home, noul=0.5)
    os.chmod(state_home / dir_to_open, mode)
    try:
        code, out, _ = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    finally:
        planted.close()
    # The planted socket gets nothing; the server task-cli starts answers instead.
    assert planted.requests == []
    assert code == 0 and json.loads(out)["engines"]["laya"]["socket"] == str(laya_server.path)
    assert task_calls(tmp_path) == [["classify-server", "start"]]


@pytest.mark.parametrize("reply", [
    b'{"ok": true}\n',
    b'{"ok": false, "error": "ValueError"}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": "high"}}}}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": NaN}}}}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": 7}}}}\n',
    b"not json\n",
    b"[]\n",
])
def test_malformed_reply_exits_2(state_home, capsys, monkeypatch, tmp_path, reply):
    server = serve(state_home, raw_reply=reply)
    try:
        code, out, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    assert code == 2 and out == "" and "Laya" in err
    # The server is up: one request, and no start.
    assert len(server.requests) == 1 and task_calls(tmp_path) == []


def test_silent_server_times_out_with_exit_2(state_home, capsys, monkeypatch):
    monkeypatch.setenv("TEXT_LINT_LAYA_TIMEOUT_MS", "300")
    server = serve(state_home, hang=True)
    try:
        code, _, err = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    finally:
        server.close()
    assert code == 2 and "Laya" in err


def test_timeout_comes_from_the_profile_unless_overridden(monkeypatch):
    monkeypatch.delenv("TEXT_LINT_LAYA_TIMEOUT_MS", raising=False)
    assert laya.request_timeout_s(1500) == 1.5
    monkeypatch.setenv("TEXT_LINT_LAYA_TIMEOUT_MS", "250")
    assert laya.request_timeout_s(1500) == 0.25


def test_bad_timeout_env_exits_2(capsys, monkeypatch, fake_engines):
    monkeypatch.setenv("TEXT_LINT_LAYA_TIMEOUT_MS", "soon")
    monkeypatch.setattr("sys.stdin", io.StringIO("Plain text.\n"))
    assert main(["check", "--profile", "issue"]) == 2
    assert "TEXT_LINT_LAYA_TIMEOUT_MS" in capsys.readouterr().err


def test_socket_removed_between_glob_and_stat_starts_the_server(state_home, capsys, monkeypatch, tmp_path):
    directory = laya.socket_dir()
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory.parent, 0o700)
    glob = Path.glob
    # Only the socket directory: the native rule packs are found with Path.glob too.
    monkeypatch.setattr(Path, "glob", lambda self, pattern: iter([self / "laya-0.22.0.sock"])
                        if self == directory else glob(self, pattern))
    code, _, _ = run(ISSUE_JSON, capsys, monkeypatch, "Plain text.\n")
    assert code == 0
    assert task_calls(tmp_path) == [["classify-server", "start"]]


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


def test_fake_task_is_first_on_path(laya_server, tmp_path):
    # No test reaches the real task-cli: `task` resolves to tests/fakes/task.
    import subprocess

    out = subprocess.run([shutil.which("task"), "classify-server", "start"], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and out.stdout.strip() == str(laya_server.path)
    assert task_calls(tmp_path) == [["classify-server", "start"]]


@pytest.mark.parametrize("reply", [
    b'{"ok": true, "result": {"answers": {"slop": {"noul": "0.5"}}}}\n',
    b'{"ok": true, "result": {"answers": {"slop": {"noul": true}}}}\n',
    b'{"ok": 1, "result": {"answers": {"slop": {"noul": 0.5}}}}\n',
])
def test_wire_reply_requires_boolean_ok_and_numeric_probability(laya_server, reply):
    # tg-cli parseLayaReply accepts ok === true and typeof p === "number" only.
    laya_server.raw_reply = reply
    with pytest.raises(laya.LayaUnavailable):
        laya.advise("Plain text.", question="slop")


def test_advise_uses_sixty_second_cold_start_budget(state_home, monkeypatch, laya_server):
    waits = []

    def fake_start(wait_s):
        waits.append(wait_s)
        return laya_server.path

    monkeypatch.setattr(laya, "_start", fake_start)
    assert laya.advise("Plain text.", question="slop")["noul"] == 0.1
    assert len(waits) == 1 and 59.9 < waits[0] <= 60.0


def test_ci_does_not_hide_a_broken_warm_server(capsys, monkeypatch, fake_engines, laya_server, tmp_path):
    monkeypatch.setenv("CI", "true")
    laya_server.raw_reply = b'not json\n'
    code, out, err = run([*ISSUE_JSON, "--laya-mode", "ci"], capsys, monkeypatch, "Plain text.")
    assert code == 2 and out == "" and "Laya" in err
    assert len(laya_server.requests) == 1 and task_calls(tmp_path) == []


def test_warm_request_deadline_includes_all_reply_chunks(laya_server):
    laya_server.raw_reply = b'{"ok":true,"result":{"answers":{"slop":{"noul":0.1}}}}\n'
    laya_server.chunk_delay = 0.02
    started = time.monotonic()
    with pytest.raises(laya.LayaUnavailable):
        laya.advise("Plain text.", question="slop", timeout_s=0.15)
    assert time.monotonic() - started < 0.6


@pytest.mark.parametrize("reply", [
    b'{"ok":true,"result":{"answers":[]}}\n',
    b'{"ok":true,"result":{"answers":""}}\n',
    b'{"ok":true,"result":[]}\n',
    b'{"ok":true,"result":""}\n',
    b'{"ok":true,"result":{"answers":{"slop":{"noul":0.2}}}}',
])
@pytest.mark.parametrize("automated", [False, True])
def test_wire_requires_objects_and_newline(reply, automated, capsys, monkeypatch, fake_engines, laya_server,
                                           tmp_path):
    laya_server.raw_reply = reply
    code, out, err = run([*ISSUE_JSON, "--input-json"], capsys, monkeypatch,
                         input_doc([("body", "Plain text.", "plain")], automated=automated))
    if automated:
        assert code == 0
        assert json.loads(out)["engines"]["laya"]["status"] == "unreachable"
    else:
        assert code == 2 and out == "" and "Laya" in err
    assert len(laya_server.requests) == 1 and task_calls(tmp_path) == []


class BudgetClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def spend(self, duration, allowance):
        self.now += min(duration, allowance)
        if duration > allowance:
            raise TimeoutError("deterministic delayed operation")


class BudgetConnection:
    def __init__(self, clock, timeout, reply_delay=0.06):
        self.clock, self.timeout, self.reply_delay = clock, timeout, reply_delay

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def sendall(self, request):
        pass

    def recv(self, count):
        self.clock.spend(self.reply_delay, self.timeout)
        return b'{"ok":true,"result":{"answers":{"slop":{"noul":0.1}}}}\n'


def test_warm_budget_includes_connect_and_reply(monkeypatch):
    clock = BudgetClock()
    monkeypatch.setattr(laya, "time", clock)

    def connect(path, timeout):
        clock.spend(0.06, timeout)
        return BudgetConnection(clock, timeout)

    monkeypatch.setattr(laya, "_connect", connect)
    with pytest.raises(laya.LayaUnavailable):
        laya.advise("Plain text.", question="slop", timeout_s=0.1)
    assert clock.now == pytest.approx(0.1)


@pytest.mark.parametrize("discovery_delay", [0.0, 0.06])
def test_candidates_share_discovery_and_connect_budget(monkeypatch, discovery_delay, tmp_path):
    clock, allowances = BudgetClock(), []
    monkeypatch.setattr(laya, "time", clock)
    monkeypatch.delenv("TEXT_LINT_LAYA_SOCKET")

    def candidates():
        clock.now += discovery_delay
        return [tmp_path / "laya-2.sock", tmp_path / "laya-1.sock"]

    def connect(path, timeout):
        allowances.append(timeout)
        try:
            clock.spend(0.06, timeout)
        except TimeoutError:
            pass

    monkeypatch.setattr(laya, "candidate_sockets", candidates)
    monkeypatch.setattr(laya, "_connect", connect)
    with pytest.raises(laya.LayaNotListening):
        laya.ask("Plain text.", ["slop"], timeout_s=0.1, start=False)
    assert clock.now == pytest.approx(0.1)
    assert allowances == pytest.approx([0.1, 0.04] if discovery_delay == 0 else [0.04])


def test_cold_start_budget_includes_initial_discovery(monkeypatch, laya_server):
    clock, waits = BudgetClock(), []
    monkeypatch.setattr(laya, "time", clock)
    monkeypatch.delenv("TEXT_LINT_LAYA_SOCKET")

    def no_listener(deadline):
        clock.now += 0.06
        return None, None

    def start(wait_s):
        waits.append(wait_s)
        clock.now += 0.5
        return laya_server.path

    def connect(path, timeout):
        clock.spend(0.02, timeout)
        return BudgetConnection(clock, timeout, reply_delay=0.02)

    monkeypatch.setattr(laya, "_connect_highest", no_listener)
    monkeypatch.setattr(laya, "_start", start)
    monkeypatch.setattr(laya, "_connect", connect)
    assert laya.ask("Plain text.", ["slop"], timeout_s=0.1)["answers"] == {"slop": 0.1}
    assert waits == pytest.approx([59.94])
    assert clock.now == pytest.approx(0.6)
