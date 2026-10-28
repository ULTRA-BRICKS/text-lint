"""Regressions for the API candidate's first review."""
import io
import json
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
from conftest import FAKES, FakeLaya
from test_parity import _intent_mismatch

from text_lint import check
from text_lint.check import check_units
from text_lint.cli import main
from text_lint.inputs import Meta, parse_input_json, unit_from_text
from text_lint.native import formats, intent
from text_lint.profiles import load_profile


def test_fake_laya_closes_exact_resources():
    servers = []
    try:
        with tempfile.TemporaryDirectory() as directory:
            for i in range(12):
                server = FakeLaya(Path(directory) / str(i))
                servers.append(server)
                for _ in range(3 if i % 3 else 0):
                    with socket.socket(socket.AF_UNIX) as peer:
                        peer.settimeout(2)
                        peer.connect(str(server.path))
                        peer.sendall(b'{"questions": {}}\n' if i % 3 == 1 else b'invalid\n')
                        if i % 3 == 1:
                            assert peer.recv(1024)
                server.close()
                server.close()
                assert not server.thread.is_alive()
                assert server.sock.fileno() == -1
    finally:
        # Also wake the old implementation on RED, so the regression itself leaves no thread.
        for server in servers:
            try:
                server.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            server.close()


def test_fake_laya_closes_partial_peer():
    with tempfile.TemporaryDirectory() as directory:
        server = FakeLaya(Path(directory) / 's')
        with socket.socket(socket.AF_UNIX) as peer:
            peer.connect(str(server.path))
            peer.sendall(b'{')
            try:
                server.close()
                assert not server.thread.is_alive()
                assert server.sock.fileno() == -1
            finally:
                peer.shutdown(socket.SHUT_RDWR)
                server.close()


@pytest.mark.parametrize('mode', ['normal', 'none', 'partial', 'invalid'])
def test_cold_task_returns_ready_listener(mode, tmp_path, monkeypatch, cold_task_child):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 's'
        monkeypatch.setenv('FAKE_TASK_SOCKET', str(path))
        monkeypatch.setenv('FAKE_TASK_CREATE_SOCKET', '1')
        monkeypatch.setenv('FAKE_TASK_REQUEST_LOG', str(tmp_path / 'request'))
        result = subprocess.run([str(FAKES / 'task'), 'classify-server', 'start'],
                                capture_output=True, text=True, timeout=2, check=True)
        assert result.stdout.strip() == str(path)
        if mode == 'none':
            return  # Fixture cleanup must stop a child still blocked in accept().
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(2)
            peer.connect(str(path))  # No wait: the returned path must already accept.
            if mode == 'normal':
                peer.sendall(b'{"questions": {"slop": {}}}\n')
                assert json.loads(peer.recv(4096))['ok'] is True
            elif mode == 'partial':
                peer.sendall(b'{')
            elif mode == 'invalid':
                peer.sendall(b'invalid\n')


@pytest.mark.parametrize('document', [False, True])
def test_original_source_spans(document, fake_engines):
    raw = '\ufeff😀 first\r\nsecond\r\n😀 ка日eat utilize'
    value = {'schema': 'text-lint/input@1', 'fields': [
        {'name': 'body', 'text': raw, 'format': 'plain'},
        {'name': 'caption', 'text': raw, 'format': 'html'}]} if document else raw
    report = check(value, profile='tg-bot')
    for rule, match in [('Script.StrayCjk', '日'), ('TextLint.Utilize', 'utilize')]:
        hits = [f for f in report['findings'] if f['rule_id'] == rule]
        assert len(hits) == (2 if document else 1)
        for hit in hits:
            start = raw.index(match)
            assert hit['span'] == [start, start + len(match)]
            assert (hit['line'], hit['col']) == (3, start - raw.rfind('\n', 0, start))
            assert raw[slice(*hit['span'])] == match


@pytest.mark.parametrize('entry', ['decode', 'intent', 'units', 'api', 'cli'])
def test_numeric_entity_all_entry_points(entry, fake_engines, monkeypatch, capsys):
    raw = '&#' + '0' * 5000 + '65;'
    doc = {'schema': 'text-lint/input@1', 'fields': [
        {'name': 'body', 'text': raw, 'format': 'html'}], 'meta': {'agent_authored': True}}
    if entry == 'decode':
        assert formats.decode_entities(raw) == 'A'
    elif entry == 'intent':
        assert intent.worth_asking(raw, Meta(agent_authored=True)) is not None
    elif entry == 'units':
        units, meta = parse_input_json(json.dumps(doc))
        assert check_units(units, meta, load_profile('tg-bot'), enforce='block')['schema'] == 'text-lint/report@1'
    elif entry == 'api':
        assert check(doc, profile='tg-bot')['schema'] == 'text-lint/report@1'
    else:
        monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(doc)))
        assert main(['check', '--input-json', '--profile', 'tg-bot', '--format', 'json']) in (0, 1)
        out, err = capsys.readouterr()
        assert json.loads(out)['schema'] == 'text-lint/report@1'
        assert 'Traceback' not in err


@pytest.mark.parametrize('n', [100, 400])
def test_separator_rows_are_split_once(n):
    tables = formats.table_option_rows('| --- | --- |\n' * n)
    assert [len(rows) for rows in tables] == list(range(n - 2, -1, -1))  # Reference overlap.
    distinct = {id(row) for rows in tables for row in rows}
    assert len(distinct) == n - 2  # Each source line became one row, however many grids hold it.


# Expected values come from the unchanged TypeScript reference (features/cli/escalation-format.ts,
# tableOptionRows and decisionLabels) run with bun on these bodies; none was recaptured here.
HEADER = '| option | tradeoff |\n'
DASHES = '| --- | --- |\n'


@pytest.mark.parametrize('body,labels', [
    (HEADER + DASHES + '| A | fast |\n| B | safe |\n', ['decision 1']),
    (HEADER + DASHES + DASHES + '| A | fast |\n| B | safe |\n', ['decision 1', 'decision 2']),
    (HEADER + DASHES + DASHES + DASHES + '| A | x |\n| B | y |\n',
     ['decision 1', 'decision 2', 'decision 3']),
    (HEADER + DASHES + '| A | f |\n| B | s |\n\n' + HEADER + DASHES + '| C | f |\n| D | s |\n',
     ['decision 1', 'decision 2']),
], ids=['ordinary', 'one-dashes-row', 'two-dashes-rows', 'adjacent-grids'])
def test_dashes_only_grid_rows_keep_reference_decision_labels(body, labels):
    assert formats.decision_labels(body) == labels


def test_dashes_only_row_opens_the_reference_suffix_grids():
    body = HEADER + DASHES + DASHES + DASHES + '| A | x |\n| B | y |\n'
    dashes = ['---', '---']
    assert formats.table_option_rows(body) == [
        [dashes, dashes, ['A', 'x'], ['B', 'y']], [dashes, ['A', 'x'], ['B', 'y']], [['A', 'x'], ['B', 'y']]]


def test_grids_preserve_ordinary_results():
    grid = '| name | tradeoff |\n| --- | --- |\n| A | fast |\n| B | safe |'
    assert formats.table_option_rows(grid + '\n\n' + grid) == [
        [['A', 'fast'], ['B', 'safe']], [['A', 'fast'], ['B', 'safe']]]
    # Preserve legacy treatment of escaped pipes and fenced grids.
    assert formats.table_option_rows('```\n' + grid.replace('fast', r'fast\|cheap') + '\n```') == [
        [['A', 'fast\\', 'cheap'], ['B', 'safe']]]


def _peak_bytes(work):
    """Peak bytes allocated while ``work`` runs."""
    import tracemalloc
    tracemalloc.start()
    try:
        work()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_positions_share_one_line_index():
    n = 3000  # One rebuilt line index would hold 3,001 starts, over 100 KB.
    unit = unit_from_text('😀 bad\r\n' * n, 'plain')
    assert unit.position(2, space='raw') == (1, 3)  # The first query pays for the index.

    def query_all():
        for i in range(n):
            assert unit.position(i * 7 + 2, space='raw') == (i + 1, 3)

    assert _peak_bytes(query_all) < 16 * 1024


def test_nullable_intent_key_must_exist():
    case = {'intent': {'valid': None}}
    assert _intent_mismatch(case, {'intent': {'valid': None}}) is None
    assert _intent_mismatch(case, {'intent': {}}) is not None


@pytest.mark.parametrize('fmt', ['plain', 'html', 'markdown'])
def test_api_normalized_newline_span_covers_crlf(fmt):
    from text_lint.inputs import unit_from_api_text
    unit = unit_from_api_text('\ufeff😀\r\nx\r\n', fmt)
    assert unit.text == '😀\nx\n'
    assert unit.raw_span(0, 2) == (1, 4)
    assert unit.raw_span(2, 4) == (4, 7)


@pytest.mark.parametrize('mode', ['partial', 'hang', 'chunked', 'error'])
def test_fake_laya_close_releases_every_peer_resource(mode):
    import os
    import threading
    import time

    def resources():
        return len(os.listdir('/proc/self/fd')), threading.active_count()

    before = resources()
    with tempfile.TemporaryDirectory() as directory:
        server = FakeLaya(Path(directory) / 's', hang=mode == 'hang',
                          raw_reply=b'{}\n' if mode == 'chunked' else None)
        if mode == 'chunked':
            server.chunk_delay = 30  # close must interrupt this wait, not sleep through it.
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(2)
                client.connect(str(server.path))
                client.sendall(b'{' if mode == 'partial' else b'{"questions": {}}\n')
                deadline = time.monotonic() + 2
                while server.peer is None and not server.requests:
                    assert time.monotonic() < deadline, 'the fake never accepted the client'
                    time.sleep(.005)
                if mode == 'error':
                    client.shutdown(socket.SHUT_RDWR)
                server.close()
                server.close()
                assert not server.thread.is_alive()
                assert server.sock.fileno() == -1
                assert server.peer is None
                while client.recv(4096):  # Any reply already sent, then the end of the stream:
                    pass  # the server's side of the connection is really closed.
        finally:
            server.close()
    assert resources() == before  # No leaked peer socket, buffered reader or thread.


def test_source_mapping_metadata_and_external_scaling():
    from dataclasses import replace

    from text_lint.check import _Document
    from text_lint.inputs import unit_from_api_text
    from text_lint.report import Finding
    n = 3000  # A rebuilt line index per finding would hold over 100 KB.
    raw = '\ufeff' + '😀 bad\r\n' * n
    unit = replace(unit_from_api_text(raw, 'plain'), field='body', path='source', key='message',
                   gate=False, line_offset=10)
    doc = _Document([unit])

    def locate_all():
        for i in range(n):
            hit = doc.locate(Finding(rule_id='test', engine='vale', severity='warning',
                                    line=i + 1, col=3, span=(i * 6 + 2, i * 6 + 5),
                                    match='bad', message='test'))
            assert hit.span == (i * 7 + 3, i * 7 + 6)
            assert (hit.line, hit.col) == (i + 11, 4 if i == 0 else 3)
            assert (hit.field, hit.path, hit.key, hit.gate) == ('body', 'source', 'message', False)

    locate_all()  # The first pass builds the unit's index.
    assert _peak_bytes(locate_all) < 32 * 1024
    mapped = replace(unit, raw_map=[(50, i + 1) for i in range(len(raw))])

    def mapped_positions():
        assert mapped.position(3, space='raw') == (50, 4)
        assert mapped.position(len(raw), space='raw') == (50, len(raw) + 1)

    assert _peak_bytes(mapped_positions) < 16 * 1024  # An explicit source map needs no line index.
    other = replace(unit, raw='x\ny')
    assert other.position(2, space='raw') == (12, 1)  # A replaced unit never reuses the old index.
