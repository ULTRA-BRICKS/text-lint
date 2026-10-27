"""Regressions for the independently reviewed Task2–4 foundation.

Expected matcher/classifier outputs were captured from the original TS exports with
Bun 1.4.2; see oracles/capture_foundation.py and fixtures/foundation-source-oracle.json.
"""
import io
import json
from pathlib import Path

import pytest

from text_lint.check import check_units
from text_lint.cli import main
from text_lint.engines import native as engine
from text_lint.inputs import Meta, unit_from_text
from text_lint.native import KINDS, PACK_DIR, NativeRule, dictionary, load_pack
from text_lint.native.pattern import compile_pattern
from text_lint.profiles import ConfigError, load_profile

ORACLE = json.loads((Path(__file__).parent / 'fixtures/foundation-source-oracle.json').read_text())
NULLABLE_REPEAT = {
    '(?:|a)*', '(?:|a)+', '(?:|a)?', '(?:|a){0,2}', '(?:|a){2}', '(?:|a)*?',
    '(?:a?)*', '(?:(?=a)|a)*', '(?:(?:)|a)*', '(?:a*|b)+',
}
UNSUPPORTED = NULLABLE_REPEAT | {r'\p{L}+', '(?<name>a)', r'(a)\1', 'a{4294967296}',
               r'\u{D83D}\uDE00', r'[^\u{D83D}\uDE00]'}


def rule(pattern='fork', **params):
    return NativeRule('Test.Pattern', 'pattern', 'error', ['chat'], 'banned {match}',
                      {'pattern': pattern, **params}, {})


@pytest.mark.parametrize('case', ORACLE['patterns'], ids=lambda c: repr((c['pattern'], c['flags'])))
def test_regex_source_oracle(case):
    if case['kind'] == 'error' or case['pattern'] in UNSUPPORTED:
        with pytest.raises(ValueError, match='invalid regex|unsupported'):
            compile_pattern(case['pattern'], case['flags'])
    else:
        unit = unit_from_text(case['text'], 'plain')
        found = engine.run([unit], Meta(), [rule(case['pattern'], flags=case['flags'])])
        assert [f.match for f in found] == case['matches']


@pytest.mark.parametrize('case', ORACLE['scripts'], ids=lambda c: ascii(c['text']))
def test_unicode17_source_classifier(case):
    mixed = next(r for r in load_pack(PACK_DIR / 'script') if r.kind == 'mixed-script')
    found = engine.run([unit_from_text(case['text'], 'plain')], Meta(), [mixed])
    expected = case['result']['tokens'] if case['result'] else []
    assert [(f.match, f.span[0]) for f in found] == [(v['token'], v['index']) for v in expected]


def test_public_kind_interface_keeps_script_scope():
    r = rule(only_if_script='cyrillic')
    unit = unit_from_text('fork', 'plain', field='body')
    assert [f.match for f in KINDS['pattern'](r, unit, Meta(), 'Проверка\n\nfork')] == ['fork']
    assert KINDS['pattern'](r, unit, Meta(), 'fork') == []
    ungated = unit_from_text('fork', 'plain', field='title', gate=False)
    assert KINDS['pattern'](r, ungated, Meta(), 'Проверка\n\nfork') == []


def test_public_kind_interface_all_registered_kinds():
    for pack in ('script', 'formats', 'intent', 'structure'):
        for r in load_pack(PACK_DIR / pack):
            unit = unit_from_text('Ordinary prose.', 'plain')
            assert isinstance(KINDS[r.kind](r, unit, Meta(), unit.raw), list)


@pytest.mark.parametrize('source', ['dictionary', 'pack'])
def test_regex_overflow_is_configuration_error_with_cli_exit2(tmp_path, capsys, monkeypatch, source):
    if source == 'dictionary':
        path = tmp_path / 'overflow-DICT.json'
        path.write_text(json.dumps({'version': 1, 'rules': [
            {'id': 'overflow', 'pattern': 'a{4294967296}', 'replacement': 'x'}]}))
        load = lambda: dictionary.load(path, True)
        argv = ['check', '--profile', 'chat', '--dict', str(path)]
        rule_id = 'overflow'
    else:
        pack = tmp_path / 'script'
        pack.mkdir()
        path = pack / 'Overflow.yml'
        path.write_text('id: Test.Overflow\nkind: pattern\nseverity: error\nprofiles: [chat]\n'
                        'message: bad\nparams: {pattern: "a{4294967296}"}\n'
                        'examples: {flag: [a], pass: [b]}\n')
        load = lambda: load_pack(pack)
        monkeypatch.setattr(engine, 'PACK_DIR', tmp_path)
        argv = ['check', '--profile', 'chat']
        rule_id = 'Test.Overflow'
    with pytest.raises(ConfigError) as exc:
        load()
    assert str(path) in str(exc.value) and rule_id in str(exc.value)
    assert 'invalid regex' in str(exc.value)
    monkeypatch.setattr('sys.stdin', io.StringIO('Ordinary prose.'))
    assert main(argv) == 2
    out, err = capsys.readouterr()
    assert out == '' and str(path) in err and rule_id in err and 'invalid regex' in err
    assert 'Traceback' not in err


def test_vale_exactly_one_process_and_optional_metadata(fake_engines):
    units = [unit_from_text('Ordinary prose.', 'plain', field='body'),
             unit_from_text('A title.', 'plain', field='title')]
    report = check_units(units, Meta(), load_profile('prose'), enforce='block')
    calls = [json.loads(line) for line in (fake_engines / 'vale.log').read_text().splitlines()]
    assert len(calls) == 1  # Includes --version and every other invocation.
    assert report['engines']['vale'] == {'status': 'ok'}
    import jsonschema
    schema = json.loads((PACK_DIR.parent.parent / 'schema/report-v1.json').read_text())
    jsonschema.validate(report, schema)


def test_ordinary_whitespace_still_skips_dictionary_and_engines(tmp_path, fake_engines, laya_server):
    path = tmp_path / 'space-DICT.json'
    path.write_text(json.dumps({'version': 1, 'rules': [
        {'id': 'space', 'pattern': ' +', 'replacement': 'x'}]}))
    report = check_units([unit_from_text('   ', 'plain')], Meta(), load_profile('chat'),
                         enforce='block', dict_path=str(path))
    assert report['verdict'] == 'pass' and report['findings'] == []
    assert {v['status'] for v in report['engines'].values()} == {'skipped'}
    assert not (fake_engines / 'vale.log').exists()
    assert not (fake_engines / 'aiwru.log').exists()
    assert laya_server.requests == []


@pytest.mark.parametrize('pattern', [r'\u{D83D}\uDE00', r'[^\u{D83D}\uDE00]'])
@pytest.mark.parametrize('source', ['dictionary', 'pack'])
def test_braced_surrogate_error_names_file_rule_and_cli_exit2(tmp_path, capsys, monkeypatch, pattern, source):
    if source == 'dictionary':
        path = tmp_path / 'surrogate-DICT.json'
        path.write_text(json.dumps({'version': 1, 'rules': [
            {'id': 'surrogate', 'pattern': pattern, 'replacement': 'x'}]}))
        load = lambda: dictionary.load(path, True)
        argv = ['check', '--profile', 'chat', '--dict', str(path)]
        rule_id = 'surrogate'
    else:
        pack = tmp_path / 'script'
        pack.mkdir()
        path = pack / 'Surrogate.yml'
        path.write_text(json.dumps({'id': 'Test.Surrogate', 'kind': 'pattern', 'severity': 'error',
                                    'profiles': ['chat'], 'message': 'bad', 'params': {'pattern': pattern},
                                    'examples': {'flag': ['a'], 'pass': ['b']}}))
        load = lambda: load_pack(pack)
        monkeypatch.setattr(engine, 'PACK_DIR', tmp_path)
        argv = ['check', '--profile', 'chat']
        rule_id = 'Test.Surrogate'
    with pytest.raises(ConfigError, match='unsupported.*surrogate') as exc:
        load()
    assert str(path) in str(exc.value) and rule_id in str(exc.value)
    monkeypatch.setattr('sys.stdin', io.StringIO('😀'))
    assert main(argv) == 2
    out, err = capsys.readouterr()
    assert out == '' and str(path) in err and rule_id in err
    assert 'unsupported' in err and 'surrogate' in err and 'Traceback' not in err


@pytest.mark.parametrize('source', ['dictionary', 'pack'])
def test_nullable_repeat_error_names_file_rule_and_cli_exit2(tmp_path, capsys, monkeypatch, source):
    pattern = '(?:|a)*'
    if source == 'dictionary':
        path = tmp_path / 'nullable-DICT.json'
        path.write_text(json.dumps({'version': 1, 'rules': [
            {'id': 'nullable', 'pattern': pattern, 'replacement': 'x'}]}))
        load = lambda: dictionary.load(path, True)
        argv = ['check', '--profile', 'chat', '--dict', str(path)]
        rule_id = 'nullable'
    else:
        pack = tmp_path / 'script'
        pack.mkdir()
        path = pack / 'Nullable.yml'
        path.write_text(json.dumps({'id': 'Test.Nullable', 'kind': 'pattern', 'severity': 'error',
                                    'profiles': ['chat'], 'message': 'bad', 'params': {'pattern': pattern},
                                    'examples': {'flag': ['a'], 'pass': ['b']}}))
        load = lambda: load_pack(pack)
        monkeypatch.setattr(engine, 'PACK_DIR', tmp_path)
        argv = ['check', '--profile', 'chat']
        rule_id = 'Test.Nullable'
    with pytest.raises(ConfigError, match='unsupported.*quantified expression.*empty') as exc:
        load()
    assert str(path) in str(exc.value) and rule_id in str(exc.value)
    monkeypatch.setattr('sys.stdin', io.StringIO('a'))
    assert main(argv) == 2
    out, err = capsys.readouterr()
    assert out == '' and str(path) in err and rule_id in err
    assert 'unsupported' in err and 'empty' in err and 'Traceback' not in err
