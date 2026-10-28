"""Engine coordinates, filesystem paths and isolated temporary-file contracts."""

import io
import json
import os
import re
import sys

import pytest

import text_lint
from text_lint.engines import EngineError, laya, vale
from text_lint.inputs import unit_from_text
from text_lint.text import LineIndex


@pytest.fixture
def cr_vale(tmp_path, monkeypatch):
    # Vale 3.24.0 runtime probe: CR, CRLF and LF each advance one line.
    binary = tmp_path / "vale"
    binary.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json, re, sys
from pathlib import Path
report = {}
for name in sys.argv[sys.argv.index('--output=JSON') + 1:]:
    text = Path(name).read_bytes().decode()
    alerts = []
    for number, line in enumerate(re.split(r'\\r\\n|\\r|\\n', text), 1):
        for hit in re.finditer('utilize', line):
            alerts.append({'Check': 'Probe.Word', 'Severity': 'warning',
                           'Line': number, 'Span': [hit.start()+1, hit.end()],
                           'Match': hit.group(), 'Message': 'Found token.'})
    report[name] = alerts
print(json.dumps(report))
"""
    )
    binary.chmod(0o700)
    monkeypatch.setenv("TEXT_LINT_VALE", str(binary))
    return binary


@pytest.mark.parametrize("newline", ["\r", "\r\n", "&#13;", "&#xD;", "&#13;\n"])
def test_html_engine_and_original_coordinates(cr_vale, newline):
    raw = "<b>utilize</b>" + newline + "a utilize"
    second = "&#xD;utilize"
    doc = {
        "schema": "text-lint/input@1",
        "fields": [
            {"name": "body", "text": raw, "format": "html"},
            {"name": "tail", "text": second, "format": "html", "gate": False},
        ],
    }
    result = text_lint.check(doc, profile="tg-bot")
    hits = [hit for hit in result["findings"] if hit["rule_id"] == "Probe.Word"]
    last = raw.rindex("utilize")
    raw_line = 2 if newline.startswith("\r") or newline.endswith("\n") else 1
    raw_col = 3 if raw_line == 2 else last + 1
    assert [(h["field"], h["span"], h["line"], h["col"]) for h in hits] == [
        ("body", [3, 10], 1, 4),
        ("body", [last, last + 7], raw_line, raw_col),
        ("tail", [5, 12], 1, 6),
    ]
    unit = unit_from_text(raw, "html")
    boundary = unit.text.index("\r") if "\r" in unit.text else unit.text.index("\n")
    width = 2 if unit.text[boundary : boundary + 2] == "\r\n" else 1
    assert unit.raw_span(boundary, boundary + width) == (14, 14 + len(newline))
    assert unit.raw_span(len(unit.text), len(unit.text)) == (len(raw), len(raw))
    assert unit.position(len(unit.text), space="text") == (raw_line, raw_col + 7)


def test_crlf_line_content_and_end_column():
    index = LineIndex("ab\r\ncd\ref\n")
    assert [index.line_text(i) for i in range(1, 5)] == ["ab", "cd", "ef", ""]
    assert index.offset(1, 3) == 2
    with pytest.raises(IndexError):
        index.offset(1, 4)
    assert index.offset(2, 3) == 6
    assert index.offset(4, 1) == 10


class DictionaryPath(os.PathLike[str]):
    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return "this is not the dictionary path"


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("kind", ["custom", "direntry"])
def test_public_dictionary_pathlike(tmp_path, fake_engines, explicit, kind):
    path = tmp_path / "dictionary.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "rules": [
                    {"id": "pathlike", "pattern": "needle", "replacement": "thread"}
                ],
            }
        )
    )
    if kind == "custom":
        value = DictionaryPath(path)
    else:
        with os.scandir(tmp_path) as entries:
            value = next(entry for entry in entries if entry.name == path.name)
    env = dict(os.environ) if explicit else None
    report = text_lint.check("needle", profile="chat", dict_path=value, environment=env)
    assert any(hit["rule_id"] == "Dict.pathlike" for hit in report["findings"])


@pytest.mark.parametrize("engine", ["vale", "laya"])
@pytest.mark.parametrize("tmpdir", [None, ""])
def test_empty_tmpdir_selects_os_fallback_without_writing(monkeypatch, engine, tmpdir):
    selected = []
    env = {} if tmpdir is None else {"TMPDIR": tmpdir}

    class StopCreation(Exception):
        pass

    def capture(*args, **kwargs):
        selected.append(kwargs.get("dir"))
        raise StopCreation

    if engine == "vale":
        monkeypatch.setattr(vale, "find_vale", lambda **kwargs: "fake")
        monkeypatch.setattr(vale.tempfile, "TemporaryDirectory", capture)
        call = lambda: vale.run([("text", ".txt")], env=env)
    else:
        monkeypatch.setattr(laya.shutil, "which", lambda *args, **kwargs: "fake")
        monkeypatch.setattr(laya.tempfile, "TemporaryFile", capture)
        call = lambda: laya._start(1, env=env)
    with pytest.raises(StopCreation):
        call()
    assert selected == ["/tmp"]


@pytest.mark.parametrize("engine", ["vale", "laya"])
@pytest.mark.parametrize("kind", ["missing", "fifo", "broken-link"])
def test_invalid_tmpdir_is_public_error(
    tmp_path, fake_engines, monkeypatch, engine, kind
):
    directory = tmp_path / "invalid"
    if kind == "fifo":
        os.mkfifo(directory)
    elif kind == "broken-link":
        directory.symlink_to(tmp_path / "missing-target")
    env = dict(os.environ, TMPDIR=str(directory))
    if engine == "laya":
        env.pop("TEXT_LINT_LAYA_SOCKET")
        env["XDG_STATE_HOME"] = str(tmp_path / "no-state")
    with pytest.raises(
        text_lint.TextLintError,
        match="(?i)(temporary|temp).*(invalid|permission|directory)",
    ):
        text_lint.check(
            "hello",
            profile="chat" if engine == "laya" else "prose",
            environment=env,
        )
    if directory.is_dir():
        assert not list(directory.iterdir())


# The test's own child process: when the suite runs as root (a Runpod pod), mode bits alone
# do not deny it, so the child first drops the two capabilities that bypass them and proves
# the denial before the check runs. A child that cannot be denied fails the test.
UNWRITABLE_CHILD = r"""
import ctypes, json, os, sys

root, engine, directory = sys.argv[1:4]
sys.path.insert(0, root)
DAC_OVERRIDE, DAC_READ_SEARCH = 1, 2
if os.geteuid() == 0:
    libc = ctypes.CDLL(None, use_errno=True)

    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]

    header, data = Header(0x20080522, 0), (Data * 2)()
    assert libc.capget(ctypes.byref(header), data) == 0
    for cap in (DAC_OVERRIDE, DAC_READ_SEARCH):
        libc.prctl(24, cap, 0, 0, 0)  # PR_CAPBSET_DROP, best effort: no exec regains it
        data[0].effective &= ~(1 << cap)
        data[0].permitted &= ~(1 << cap)
        data[0].inheritable &= ~(1 << cap)
    assert libc.capset(ctypes.byref(header), data) == 0
try:
    os.close(os.open(os.path.join(directory, "probe"), os.O_WRONLY | os.O_CREAT))
    denied = False
except PermissionError:
    denied = True
import text_lint

env = dict(os.environ, TMPDIR=directory)
if engine == "laya":
    env.pop("TEXT_LINT_LAYA_SOCKET")
    env["XDG_STATE_HOME"] = os.path.join(directory, "no-state")
try:
    text_lint.check("hello", profile="chat" if engine == "laya" else "prose", environment=env)
    error = None
except text_lint.TextLintError as exc:
    error = str(exc)
print(json.dumps({"denied": denied, "error": error}))
"""


@pytest.mark.parametrize("engine", ["vale", "laya"])
def test_unwritable_tmpdir_is_public_error_even_for_root(
    tmp_path, fake_engines, engine
):
    import subprocess
    from pathlib import Path

    directory = tmp_path / "unwritable"
    directory.mkdir(mode=0o500)
    try:
        done = subprocess.run(
            [
                sys.executable,
                "-c",
                UNWRITABLE_CHILD,
                str(Path(__file__).resolve().parent.parent),
                engine,
                str(directory),
            ],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        outcome = json.loads(done.stdout.splitlines()[-1])
        assert outcome["denied"], "the child could still write to the directory"
        assert outcome["error"] is not None, "check ran without the temporary directory"
        assert re.search(
            "(?i)(temporary|temp).*(invalid|permission|directory)", outcome["error"]
        )
        assert os.listdir(directory) == []
    finally:
        directory.chmod(0o700)


@pytest.mark.parametrize("engine", ["vale", "laya"])
def test_tmpdir_symlink_to_private_directory(tmp_path, fake_engines, engine):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    env = dict(os.environ, TMPDIR=str(link))
    if engine == "laya":
        env.pop("TEXT_LINT_LAYA_SOCKET")
        env["XDG_STATE_HOME"] = str(tmp_path / "no-state")
    text_lint.check(
        "hello", profile="chat" if engine == "laya" else "prose", environment=env
    )
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_vale_creation_cleanup(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(vale, "find_vale", lambda **kwargs: "fake")

    def fail(*args, **kwargs):
        raise failure("injected engine file write failure")

    monkeypatch.setattr("builtins.open", fail)
    with pytest.raises(EngineError if failure is OSError else KeyboardInterrupt):
        vale.run([("hello", ".txt")], env={"TMPDIR": str(tmp_path)})
    assert not list(tmp_path.glob("text-lint-*"))


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_laya_partial_log_creation_closes_first(monkeypatch, failure):
    first = io.BytesIO()
    count = 0

    def create(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise failure("injected second log failure")
        return first

    monkeypatch.setattr(laya.shutil, "which", lambda *args, **kwargs: "fake")
    monkeypatch.setattr(laya.tempfile, "TemporaryFile", create)
    with pytest.raises(
        laya.LayaUnavailable if failure is OSError else KeyboardInterrupt
    ):
        laya._start(1, env={"TMPDIR": "synthetic"})
    assert first.closed


def test_laya_cancel_reaps_fake_start_and_closes_logs(monkeypatch):
    logs = []
    events = []

    def create(**kwargs):
        log = io.BytesIO()
        logs.append(log)
        return log

    class Process:
        def wait(self, timeout=None):
            if timeout is not None:
                raise KeyboardInterrupt
            events.append("reaped")

        def kill(self):
            events.append("killed")

    monkeypatch.setattr(laya.shutil, "which", lambda *args, **kwargs: "fake")
    monkeypatch.setattr(laya.tempfile, "TemporaryFile", create)
    monkeypatch.setattr(laya.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(KeyboardInterrupt):
        laya._start(1, env={"TMPDIR": "synthetic"})
    assert events == ["killed", "reaped"]
    assert all(log.closed for log in logs)


def test_missing_default_socket_diagnostic():
    with pytest.raises(
        laya.LayaNotListening, match="HOME.*XDG_STATE_HOME|XDG_STATE_HOME.*HOME"
    ) as exc:
        laya.ask("text", ["slop"], start=False, env={})
    assert "None" not in str(exc.value)


def test_engine_lf_mode_preserves_decoded_cr_columns():
    index = LineIndex("a\rb utilize\nend", universal=False)
    assert index.line_text(1) == "a\rb utilize"
    assert index.offset(1, 5) == 4
    assert index.line_col(4) == (1, 5)


def test_aiw_fallback_coordinates_after_decoded_cr(tmp_path, cr_vale, monkeypatch):
    # Installed aiw-ru text._line_starts counts LF only; force its index-less wire path.
    binary = tmp_path / "aiw"
    binary.write_text(
        "#!"
        + sys.executable
        + "\n"
        + "import json, sys\n"
        + "sys.stdin.read()\n"
        + "print("
        + repr(
            json.dumps(
                {
                    "issues": [
                        {
                            "text": "данный",
                            "line": 1,
                            "column": 3,
                            "type": "test",
                            "rule": "cr",
                            "severity": "P1",
                        }
                    ]
                }
            )
        )
        + ")\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("TEXT_LINT_AIWRU", str(binary))
    raw = "a&#13;данный"
    result = text_lint.check(
        {
            "schema": "text-lint/input@1",
            "fields": [{"name": "body", "text": raw, "format": "html"}],
        },
        profile="tg-bot",
    )
    hit = next(h for h in result["findings"] if h["engine"] == "aiw-ru")
    assert (hit["span"], hit["line"], hit["col"], hit["match"]) == (
        [6, 12],
        1,
        7,
        "данный",
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_pathlike_home_expansion_is_isolated(tmp_path, monkeypatch, explicit):
    home = tmp_path / "home"
    home.mkdir()
    path = home / "dict.json"
    path.write_text('{"version": 1, "rules": []}')
    monkeypatch.setenv("HOME", str(home))
    env = {"HOME": str(home), "CI": "true", "PATH": ""} if explicit else None
    if not explicit:
        monkeypatch.setenv("CI", "true")
    report = text_lint.check(
        "hello",
        profile="chat",
        dict_path=DictionaryPath("~/dict.json"),
        laya_mode="ci",
        environment=env,
    )
    assert report["verdict"] == "pass"
    if explicit:
        with pytest.raises(text_lint.TextLintError, match="needs HOME"):
            text_lint.check(
                "hello",
                profile="chat",
                dict_path=DictionaryPath("~/dict.json"),
                laya_mode="ci",
                environment={"CI": "true"},
            )


def test_default_named_user_pathlike_uses_synthetic_account(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace

    path = tmp_path / "dict.json"
    path.write_text('{"version": 1, "rules": []}')
    looked_up = []

    def account(name):
        looked_up.append(name)
        return SimpleNamespace(pw_dir=str(tmp_path))

    monkeypatch.setattr(pwd, "getpwnam", account)
    report = text_lint.check(
        "hello", profile="chat", dict_path=DictionaryPath("~synthetic/dict.json")
    )
    assert report["verdict"] == "pass"
    assert looked_up == ["synthetic"]
    with pytest.raises(
        text_lint.TextLintError, match="named-user expansion is unsupported"
    ):
        text_lint.check(
            "hello",
            profile="chat",
            dict_path=DictionaryPath("~synthetic/dict.json"),
            environment={},
        )
    assert looked_up == ["synthetic"]
