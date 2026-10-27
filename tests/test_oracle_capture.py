"""Development oracle capture must retain the legacy source as authority."""
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def harness(name):
    path = ROOT / 'tests/oracles' / (name + '.py')
    assert path.is_file(), 'native Python capture harness is required'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.usefixtures('fake_engines')
@pytest.mark.parametrize('context,profile', [('issue', 'issue'), ('docs', 'prose'), ('pr', 'pr'), ('tg', 'tg-bot')])
def test_legacy_installer_context(context, profile, monkeypatch, capsys):
    from text_lint.cli import main
    monkeypatch.setattr('sys.stdin', io.StringIO('The importer reads a file.'))
    assert main(['check', '--context', context, '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)['profile'] == profile


def test_capture_requires_explicit_checkout_and_bun():
    mod = harness('capture_foundation')
    with pytest.raises(SystemExit) as exc:
        mod.parser().parse_args([])
    assert exc.value.code == 2


def test_reject_wrong_runtime():
    mod = harness('capture_foundation')
    with pytest.raises(ValueError, match='Bun'):
        mod.validate_runtime({'bun': '1.4.3', 'revision': mod.BUN_REVISION, 'versions': {'unicode': '17.0', 'icu': '78.3'}})


def test_reject_non_checkout(tmp_path):
    mod = harness('capture_foundation')
    with pytest.raises(ValueError, match='checkout'):
        mod.validate_checkout(tmp_path)


def test_unicode_ranges_and_folds():
    mod = harness('capture_unicode')
    assert mod.ranges([0, 1, 0xD7FF, 0xE000, 0x10FFFF]) == [(0, 1), (0xD7FF, 0xD7FF), (0xE000, 0xE000), (0x10FFFF, 0x10FFFF)]
    assert mod.fold_groups([(75, 107), (8490, 107)]) == [(75, 107, 8490)]
    with pytest.raises(ValueError):
        mod.ranges([0x110000])


def oracle_environment():
    checkout, bun = os.environ.get('TEXT_LINT_ORACLE_CHECKOUT'), os.environ.get('TEXT_LINT_ORACLE_BUN')
    if not checkout or not bun:
        pytest.skip('development reproduction requires explicit pinned checkout and Bun')
    return Path(checkout), Path(bun)


def test_original_source_reproduces_immutable_fixture():
    mod = harness('capture_foundation')
    checkout, bun = oracle_environment()
    expected = json.loads((ROOT / 'tests/fixtures/foundation-source-oracle.json').read_text())
    assert mod.capture(checkout, bun) == expected


def test_unicode_boundary_probes():
    mod = harness('capture_unicode')
    checkout, bun = oracle_environment()
    from text_lint import unicode_categories as tables
    points = [0, 0x10FFFF, 0xD800, 0xDFFF, 0x1C89, 0x1C8A, 0x1E6E3]
    # Probe both ends and the adjacent codepoints of every stored range.
    for name in ('LETTERISH', 'BASE_LETTER', 'LETTER_NUMBER'):
        for start, end in zip(*getattr(tables, name)):
            points.extend(cp for cp in (start - 1, start, end, end + 1) if 0 <= cp <= 0x10FFFF)
    rows = mod.probe(checkout, bun, sorted(set(points)))
    for cp, flags, _ in rows:
        for name, hit in zip(('LETTERISH', 'BASE_LETTER', 'LETTER_NUMBER'), flags):
            assert hit == any(a <= cp <= b for a, b in zip(*getattr(tables, name))), (name, cp)


@pytest.mark.parametrize('head,dirty', [('0' * 40, ''), ('7aa28824a1371f95fb34c2dfc699794e6d7d3946', '?? injected.ts')])
def test_wrong_revision_and_dirty_checkout(tmp_path, monkeypatch, head, dirty):
    mod = harness('capture_foundation')
    answers = iter([head, dirty])
    monkeypatch.setattr(mod.subprocess, 'check_output', lambda *args, **kwargs: next(answers))
    with pytest.raises(ValueError, match='clean at'):
        mod.validate_checkout(tmp_path)


def test_capture_never_overwrites_expectations(tmp_path):
    from argparse import Namespace
    mod = harness('capture_foundation')
    output = tmp_path / 'fixture.json'
    output.write_text('immutable')
    with pytest.raises(FileExistsError):
        mod.write_capture(Namespace(output=output), 'replacement')
    assert output.read_text() == 'immutable'
