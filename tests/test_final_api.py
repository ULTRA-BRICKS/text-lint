"""Remaining public API contracts; every service and engine is test-local."""
import json
import os
from pathlib import Path

import pytest

import text_lint
from text_lint import inputs


def document(raw, fmt="plain"):
    return {"schema": "text-lint/input@1", "fields": [
        {"name": "body", "text": raw, "format": fmt}]}


@pytest.mark.parametrize("fmt,raw,expected,start", [
    ("plain", "\ufeff\ufeff😀x", "😀x", 2),
    ("markdown", "\ufeff\ufeff😀x", "😀x", 2),
    ("html", "&#xFEFF;&#65279;😀x", "😀x", 16),
    ("html", "<b>&#xFEFF;😀x</b>", "😀x", 11),
])
def test_api_boms_follow_input_contract(fmt, raw, expected, start):
    canonical = inputs.parse_input_json(json.dumps(document(raw, fmt)))[0][0]
    assert canonical.text == expected
    assert canonical.raw_span(0, 1) == (start, start + 1)
    actual = inputs.unit_from_api_text(raw, fmt)
    assert actual.text == canonical.text
    assert actual.raw is raw
    assert actual.raw_span(0, 1) == canonical.raw_span(0, 1)


def test_native_heading_verdict_and_original_span(fake_engines, laya_server):
    laya_server.answers["first_person"] = .9
    canonical = text_lint.check("# Heading", profile="as-user")
    assert canonical["verdict"] == "block"
    report = text_lint.check("\ufeff# Heading", profile="as-user")
    assert report["verdict"] == canonical["verdict"]
    hit = next(f for f in report["findings"] if f["rule_id"] == "AsUser.BotFormatting")
    assert (hit["span"], hit["line"], hit["col"], hit["match"]) == ([1, 3], 1, 2, "# ")


@pytest.mark.parametrize("as_document", [False, True])
def test_lone_cr_paragraph_native_verdict(fake_engines, as_document):
    paragraph = "The importer reads the file. " * 25
    raw = paragraph + "\r\r" + paragraph
    convert = document if as_document else lambda s: s
    canonical = text_lint.check(convert(raw.replace("\r", "\n")), profile="tg-bot")
    assert canonical["verdict"] == "pass"
    assert text_lint.check(convert(raw), profile="tg-bot") == canonical


@pytest.mark.parametrize("fmt", ["plain", "markdown", "html"])
def test_lone_cr_source_coordinates(fake_engines, fmt):
    raw = "\ufeff😀\rutilize\r\n日eat"
    report = text_lint.check(document(raw, fmt), profile="tg-bot")
    hits = {f["rule_id"]: f for f in report["findings"]}
    for rule, span, line, col in [("TextLint.Utilize", [3, 10], 2, 1),
                                  ("Script.StrayCjk", [12, 13], 3, 1)]:
        hit = hits[rule]
        assert (hit["span"], hit["line"], hit["col"], hit["field"]) == (span, line, col, "body")
        assert raw[slice(*span)] == hit["match"]


def test_bom_table_allegation_actual_native_result(fake_engines):
    raw = '<table><tr><td>' + 'x' * 400 + '</td></tr></table>'
    from text_lint.native.formats import has_wall_of_text
    assert not has_wall_of_text(raw)
    assert not has_wall_of_text('\ufeff' + raw)
    assert text_lint.check(document(raw, "html"), profile="tg-bot") == text_lint.check(
        document('\ufeff' + raw, "html"), profile="tg-bot")


def test_explicit_environment_empty_never_inherits(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(text_lint.TextLintError, match="CI=true"):
        text_lint.check("hello", profile="chat", laya_mode="ci", environment={})


def test_explicit_environment_uses_supplied_config(fake_engines, laya_server):
    env = dict(os.environ)
    env["TEXT_LINT_LAYA_TIMEOUT_MS"] = "invalid"
    before = dict(os.environ)
    with pytest.raises(text_lint.TextLintError, match="positive number"):
        text_lint.check("hello", profile="chat", environment=env)
    assert dict(os.environ) == before


def test_sender_prose_public_export():
    from text_lint.native.intent import sender_prose as canonical
    assert text_lint.sender_prose is canonical
    from text_lint.api import sender_prose
    assert sender_prose is canonical
    assert sender_prose('<p>Mine &amp; yours</p><blockquote>quoted</blockquote>`code` https://a.test') == '\nMine & yours\n    '


def _environment(tmp_path, label, socket_path):
    import sys
    home = tmp_path / label
    (home / '.claude').mkdir(parents=True)
    # An explicit private directory owned by this test: nothing here may depend on an
    # ambient TMPDIR (CI does not set one) or fall back to the invoking user's paths.
    private_tmp = home / 'tmp'
    private_tmp.mkdir()
    private_tmp.chmod(0o700)
    (home / '.claude' / 'DICT.json').write_text(json.dumps({"version": 1, "rules": [{
        "id": label, "pattern": "needle", "flags": "", "replacement": label, "why": "test"}]}))
    binary = home / 'engine'
    binary.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
with open(os.environ['CAPTURE'], 'a') as out:
    out.write(json.dumps(dict(os.environ)) + '\\n')
if 'scan' in sys.argv:
    sys.stdin.read()
    print('{"issues": [], "score": 0, "label": "test"}')
else:
    print('{}')
''')
    binary.chmod(0o700)
    return {"HOME": str(home), "PATH": str(home), "LC_ALL": "C.UTF-8",
            "TMPDIR": str(private_tmp), "CAPTURE": str(home / 'captured.jsonl'),
            "TEXT_LINT_VALE": str(binary), "TEXT_LINT_AIWRU": str(binary),
            "TEXT_LINT_LAYA_SOCKET": str(socket_path), "TEXT_LINT_LAYA_TIMEOUT_MS": "1000"}


def test_concurrent_environments_snapshot_and_observer(tmp_path, monkeypatch):
    import tempfile
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack
    from pathlib import Path

    from conftest import FakeLaya

    from text_lint.engines import aiwru, vale
    from text_lint.engines import run as run_engine

    arrived, release = threading.Barrier(3), threading.Event()
    stopped = threading.Event()
    before = dict(os.environ)
    observations = []

    def observe():
        while not stopped.wait(.001):
            if dict(os.environ) != before:
                observations.append(dict(os.environ))

    def gated(engine, argv, text, **kwargs):
        if engine == 'vale':
            arrived.wait(timeout=5)
            assert release.wait(5)
        return run_engine(engine, argv, text, **kwargs)

    monkeypatch.setattr(vale, 'run_engine', gated)
    monkeypatch.setattr(aiwru, 'run_engine', gated)
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='env')))
        servers = [FakeLaya(directory / f'{i}.sock', noul=.1 + i / 10) for i in range(2)]
        for server in servers:
            stack.callback(server.close)
        envs = [_environment(tmp_path, label, server.path) for label, server in zip(('A', 'B'), servers)]
        snapshots = [dict(env) for env in envs]
        observer = threading.Thread(target=observe)
        observer.start()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(text_lint.check, 'needle Русский', profile='tg-bot', environment=env)
                           for env in envs]
                try:
                    arrived.wait(timeout=5)
                    assert dict(os.environ) == before
                    for env in envs:
                        env.update(HOME='invalid-after-snapshot', CAPTURE='invalid-after-snapshot',
                                   TEXT_LINT_LAYA_SOCKET='invalid-after-snapshot')
                finally:
                    release.set()
                reports = [future.result(timeout=5) for future in futures]
        finally:
            stopped.set()
            observer.join(timeout=2)
        for label, report, env, server in zip(('A', 'B'), reports, snapshots, servers):
            assert report['verdict'] == 'block'
            assert {f['rule_id'] for f in report['findings']} == {'Dict.' + label}
            assert report['engines']['laya']['socket'] == str(server.path)
            assert len(server.requests) == 1
            captured = [json.loads(line) for line in Path(env['CAPTURE']).read_text().splitlines()]
            assert len(captured) == 2  # Actual Vale and aiw-ru subprocesses.
            assert captured == [env, env]
    assert not observations
    assert dict(os.environ) == before


@pytest.mark.parametrize('cancel', [False, True])
def test_environment_unchanged_during_failure_or_cancellation(tmp_path, laya_server, monkeypatch, cancel):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from text_lint.engines import vale

    entered, release = threading.Event(), threading.Event()
    env = _environment(tmp_path, 'failure', laya_server.path)
    before = dict(os.environ)

    def fail(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if cancel:
            raise asyncio.CancelledError()
        from text_lint.engines import EngineError
        raise EngineError('vale', 'test failure')

    monkeypatch.setattr(vale, 'run_engine', fail)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(text_lint.check, 'hello', environment=env)
        try:
            assert entered.wait(5)
            assert dict(os.environ) == before
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError if cancel else text_lint.TextLintError):
            future.result(timeout=5)
    assert dict(os.environ) == before


def test_explicit_home_dictionary_expansion_and_default_socket(tmp_path, laya_server):
    from pathlib import Path

    from text_lint.engines import laya
    from text_lint.native import dictionary
    env = _environment(tmp_path, 'home', laya_server.path)
    expected = Path(env['HOME']) / '.claude' / 'DICT.json'
    assert dictionary.resolve_path('~/.claude/DICT.json', env) == (expected, True)
    assert laya.socket_dir(env=env) == Path(env['HOME']) / '.local/state/task-cli/classify'
    assert laya.candidate_sockets(env={}) == []


def test_explicit_path_discovery_and_cold_subprocess_environment(tmp_path, laya_server, monkeypatch):
    import shutil
    from pathlib import Path

    from text_lint.engines import aiwru, laya, vale
    env = _environment(tmp_path, 'cold', laya_server.path)
    binary = Path(env['TEXT_LINT_VALE'])
    for name in ('vale', 'aiw-ru'):
        shutil.copy(binary, binary.parent / name)
    env.pop('TEXT_LINT_VALE')
    env.pop('TEXT_LINT_AIWRU')
    assert vale.find_vale(env=env) == str(binary.parent / 'vale')
    assert aiwru.find_aiwru(env=env) == str(binary.parent / 'aiw-ru')
    assert vale.find_vale(env={}) is None
    assert aiwru.find_aiwru(env={}) is None
    task = binary.parent / 'task'
    task.write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_SOCKET"\nprintf "%s\\n" "$HOME" > "$CAPTURE"\n')
    task.chmod(0o700)
    env['FAKE_SOCKET'] = str(laya_server.path)
    before = dict(os.environ)
    popen = laya.subprocess.Popen

    def copied_environment(*args, **kwargs):
        assert kwargs['env'] == env
        assert kwargs['env'] is not env
        return popen(*args, **kwargs)

    monkeypatch.setattr(laya.subprocess, 'Popen', copied_environment)
    assert laya._start(1, env=env) == laya_server.path
    assert Path(env['CAPTURE']).read_text().strip() == env['HOME']
    assert dict(os.environ) == before


def test_empty_environment_does_not_use_host_socket_or_dictionary(laya_server):
    requests = len(laya_server.requests)
    with pytest.raises(text_lint.TextLintError, match='task.*not on PATH'):
        text_lint.check('hello', profile='chat', environment={})
    report = text_lint.check('hello', profile='chat', laya_mode='ci', environment={'CI': 'true'})
    assert report['engines']['native']['dict'] is None
    assert report['engines']['laya']['status'] == 'not run'
    assert len(laya_server.requests) == requests


def test_check_never_calls_process_environment_writers(fake_engines, monkeypatch):
    snapshot = dict(os.environ)

    def forbidden(*args):
        raise AssertionError('a check attempted to change the process environment')

    with monkeypatch.context() as guard:
        guard.setattr(os, 'putenv', forbidden)
        guard.setattr(os, 'unsetenv', forbidden)
        assert text_lint.check('hello', profile='tg-bot', environment=snapshot)['verdict'] == 'pass'
    assert dict(os.environ) == snapshot


@pytest.mark.parametrize('fmt', ['plain', 'markdown', 'html'])
def test_semantic_end_spans_crlf_and_embedded_bom(fmt):
    raw = '\ufeff\ufeff😀\r\nx\ufeff'
    unit = inputs.unit_from_api_text(raw, fmt)
    assert unit.raw is raw
    assert unit.semantic == '😀\nx\ufeff'
    assert unit.text == '😀\nx\ufeff'
    assert [unit.semantic_span(i, i + 1) for i in range(4)] == [(2, 3), (3, 5), (5, 6), (6, 7)]
    assert [unit.raw_span(i, i + 1) for i in range(4)] == [(2, 3), (3, 5), (5, 6), (6, 7)]
    assert unit.semantic_span(4, 4) == (7, 7)
    assert unit.raw_span(4, 4) == (7, 7)
    assert unit.position(2, space='semantic') == (2, 1)
    assert unit.position(4, space='semantic') == (2, 3)


def test_html_semantic_keeps_tags_and_original_entity_endpoints():
    raw = '<b>&#xFEFF;😀&amp;</b>\r\nx\ufeff'
    unit = inputs.unit_from_api_text(raw, 'html')
    assert unit.raw is raw
    assert unit.semantic == '<b>😀&amp;</b>\nx\ufeff'
    assert unit.text == '😀&\nx\ufeff'
    assert unit.raw_span(0, 1) == (11, 12)
    assert unit.raw_span(1, 2) == (12, 17)
    assert unit.raw_span(2, 3) == (21, 23)
    assert unit.semantic_span(0, 3) == (0, 3)


def test_fields_keep_first_field_bom_contract_and_native_markup(fake_engines, laya_server):
    laya_server.answers['first_person'] = .9
    doc = {'schema': 'text-lint/input@1', 'fields': [
        {'name': 'title', 'text': '\ufefftitle', 'format': 'plain'},
        {'name': 'body', 'text': '\ufeff# Heading\r\n😀', 'format': 'markdown', 'gate': False}]}
    units, _ = inputs.parse_input_json(json.dumps(doc))
    assert units[0].semantic == 'title'
    assert units[1].semantic == '\ufeff# Heading\n😀'  # Later-field BOM is embedded in the document.
    report = text_lint.check(doc, profile='as-user')
    assert report['verdict'] == 'warn'  # Findings in this non-gating field cannot block.
    hit = next(f for f in report['findings'] if f['rule_id'] == 'AsUser.Emoji')
    assert (hit['field'], hit['span'], hit['line'], hit['col']) == ('body', [12, 13], 2, 1)


def test_decision_table_bom_review_rebuttal(fake_engines):
    from messages import COMPLIANT

    from text_lint.native.formats import validate_decision

    assert validate_decision(COMPLIANT) == []
    assert validate_decision('\ufeff' + COMPLIANT) == []
    reports = []
    for body in (COMPLIANT, '\ufeff' + COMPLIANT):
        doc = document(body, 'html')
        doc['meta'] = {'tag': 'decision', 'send_format': 'html', 'agent_authored': True}
        reports.append(text_lint.check(doc, profile='tg-bot'))
    assert reports[0]['intent'] == reports[1]['intent']
    assert reports[0]['verdict'] == reports[1]['verdict'] == 'warn'  # Existing Vale em dash advice.
    assert not any(f['engine'] == 'native' for report in reports for f in report['findings'])
    assert len(reports[0]['findings']) == len(reports[1]['findings']) > 0
    for original, prefixed in zip(reports[0]['findings'], reports[1]['findings'], strict=True):
        assert prefixed == {**original, 'span': [offset + 1 for offset in original['span']],
                            'col': original['col'] + (original['line'] == 1)}


def test_environment_scenarios_run_without_ambient_tmpdir(tmp_path):
    """CI sets no TMPDIR: the explicit-environment scenarios must not read one."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    home = tmp_path / 'home'
    home.mkdir()
    scenarios = ('test_concurrent_environments_snapshot_and_observer',
                 'test_environment_unchanged_during_failure_or_cancellation',
                 'test_explicit_home_dictionary_expansion_and_default_socket',
                 'test_explicit_path_discovery_and_cold_subprocess_environment')
    isolated = {'HOME': str(home), 'XDG_CONFIG_HOME': str(home / 'config'),
                'XDG_STATE_HOME': str(home / 'state'), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                'PYTHONDONTWRITEBYTECODE': '1'}
    assert 'TMPDIR' not in isolated
    junit = tmp_path / 'junit.xml'
    done = subprocess.run(
        [sys.executable, '-m', 'pytest', str(root / 'tests' / 'test_final_api.py'), '-q',
         '-p', 'no:cacheprovider', '-k', ' or '.join(scenarios), f'--basetemp={tmp_path / "nested"}',
         f'--junitxml={junit}'],
        cwd=root, env=isolated, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    import xml.etree.ElementTree as ET
    cases = ET.parse(junit).getroot().iter('testcase')
    outcomes = [(case.get('name'), [child.tag for child in case]) for case in cases]
    assert len(outcomes) == 5, outcomes  # concurrent, failure/cancellation x2, home, discovery
    assert all(tags == [] for _, tags in outcomes), outcomes  # none skipped, failed or errored


def _effect_environment(tmp_path, laya_server, with_socket):
    """A complete valid environment whose every effect (engine run, Laya request, Laya
    cold start) leaves a trace."""
    env = _environment(tmp_path, 'effects', laya_server.path)
    home = Path(env['HOME'])
    task = home / 'task'
    task.write_text('#!/bin/sh\necho started >> "$CAPTURE.task"\nexit 1\n')
    task.chmod(0o700)
    if not with_socket:
        del env['TEXT_LINT_LAYA_SOCKET']
        env['XDG_STATE_HOME'] = str(home / 'state')
    return env


def _effects(env, laya_server):
    capture = Path(env['CAPTURE'])
    return (capture.exists(), Path(str(capture) + '.task').exists(), laya_server.requests)


@pytest.mark.parametrize('with_socket', [True, False], ids=['laya-socket', 'cold-start'])
@pytest.mark.parametrize('name,value,message', [
    pytest.param('BAD\0NAME', 'x', "environment name #{n} must be non-empty and contain no '=' or NUL",
                 id='nul-name'),
    pytest.param('GOOD', 'tok\0en-secret',
                 "environment value for 'GOOD' contains NUL", id='nul-value'),
    pytest.param('TOKEN=secret-token', 'x', "environment name #{n} must be non-empty and contain no '=' or NUL",
                 id='equals-name'),
    pytest.param('', 'x', "environment name #{n} must be non-empty and contain no '=' or NUL",
                 id='empty-name'),
    pytest.param('\ud800', 'x', 'environment name #{n} cannot be encoded for the operating system',
                 id='unencodable-name'),
    pytest.param('GOOD', 'secret\ud800', "environment value for 'GOOD' cannot be encoded for the operating system",
                 id='unencodable-value'),
])
def test_invalid_environment_is_public_error_before_any_effect(
        tmp_path, laya_server, name, value, message, with_socket):
    env = _effect_environment(tmp_path, laya_server, with_socket)
    env[name] = value
    message = message.format(n=len(env))  # The invalid entry is added last.
    before = dict(os.environ)
    for profile in ('tg-bot', 'no-such-profile'):  # The environment is judged before config.
        with pytest.raises(text_lint.TextLintError) as caught:
            text_lint.check('hello', profile=profile, environment=env)
        assert str(caught.value) == message
        assert 'secret' not in str(caught.value) and 'secret' not in repr(caught.value.__cause__)
    assert _effects(env, laya_server) == (False, False, [])
    assert dict(os.environ) == before


def test_valid_environment_values_reach_the_engine_subprocesses(tmp_path, laya_server):
    env = _effect_environment(tmp_path, laya_server, True)
    env.update({'EQUALS': 'a=b=c', 'EMPTY': '', 'UNICODE': 'é😀 Русский', 'SPACE NAME': 'x',
                'RAW_BYTE': os.fsdecode(b'\x80')})
    snapshot = dict(env)
    assert text_lint.check('hello', profile='tg-bot', environment=env)['verdict'] == 'pass'
    captured = [json.loads(line) for line in Path(env['CAPTURE']).read_text().splitlines()]
    assert captured and all(seen == snapshot for seen in captured)  # Vale ran with exactly this.
    assert len(laya_server.requests) == 1


def test_plain_cli_strips_one_leading_bom_where_the_api_strips_all(fake_engines, tmp_path, capsys):
    """Documented legacy difference: only the API and input@1 align on all leading BOMs."""
    from text_lint.cli import main

    raw = '\ufeff\ufeffutilize'
    source = tmp_path / 'two-boms.txt'
    source.write_text(raw, encoding='utf-8')
    assert main(['check', '--format', 'json', '--profile', 'tg-bot', str(source)]) == 0
    cli_hit = json.loads(capsys.readouterr().out)['findings'][0]
    api_hit = text_lint.check(raw, profile='tg-bot')['findings'][0]
    assert cli_hit['span'] == [1, 8]  # One BOM read away: spans index the text as read.
    assert api_hit['span'] == [2, 9]  # Spans index the string as supplied, both BOMs included.
