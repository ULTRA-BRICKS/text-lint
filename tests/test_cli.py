"""CLI behavior against fake engines (tests/fakes): routing, output shape, exit codes."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from text_lint import __version__
from text_lint.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# tests/conftest.py: fake vale and aiw-ru for every test here.
pytestmark = pytest.mark.usefixtures("fake_engines")


def invoke(argv, capsys, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def run(argv, capsys, stdin=None, monkeypatch=None):
    """`text-lint check` with ``argv``."""
    return invoke(["check", *argv], capsys, stdin, monkeypatch)


def run_json(argv, capsys, stdin=None, monkeypatch=None):
    code, out, err = run(["--format", "json", *argv], capsys, stdin, monkeypatch)
    return code, json.loads(out), err


def logged(tmp_path, name):
    path = tmp_path / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_clean_text_exits_0(capsys, monkeypatch):
    code, data, _ = run_json([], capsys, "The importer reads one file per run.\n", monkeypatch)
    assert code == 0
    assert data["findings"] == []
    assert data["score"] == 0


def test_findings_exit_1_with_full_finding_shape(capsys, monkeypatch):
    text = "Intro.\nThis Great question is robust.\n"
    code, data, _ = run_json([], capsys, text, monkeypatch)
    assert code == 1
    assert set(data) == {"version", "score", "findings", "engines"}
    assert data["version"] == __version__
    assert data["score"] > 0
    first = data["findings"][0]
    assert set(first) == {"rule_id", "engine", "severity", "line", "col", "span", "match", "message", "suggestion"}
    assert first["rule_id"] == "TextLint.Sycophancy"
    assert first["engine"] == "vale"
    assert (first["line"], first["col"]) == (2, 6)
    start, end = first["span"]
    assert text[start:end] == "Great question" == first["match"]
    assert data["engines"]["vale"]["status"] == "ok"


def test_vale_replace_action_becomes_suggestion(capsys, monkeypatch):
    _, data, _ = run_json([], capsys, "We utilize caches.\n", monkeypatch)
    assert data["findings"][0]["suggestion"] == "use"


def test_file_and_stdin_give_the_same_findings(capsys, monkeypatch, tmp_path):
    text = (FIXTURES / "en_slop.md").read_text(encoding="utf-8")
    _, from_stdin, _ = run_json(["-"], capsys, text, monkeypatch)
    _, from_file, _ = run_json([str(FIXTURES / "en_slop.md")], capsys)
    assert from_stdin["findings"] == from_file["findings"] != []


def test_text_output_lines_and_score(capsys, monkeypatch):
    code, out, _ = run([], capsys, "We utilize Great question.\n", monkeypatch)
    lines = out.splitlines()
    assert code == 1
    assert lines[0].startswith("<stdin>:1:4 TextLint.Utilize fake TextLint.Utilize: 'utilize' → use")
    assert lines[1].startswith("<stdin>:1:12 TextLint.Sycophancy ")
    assert lines[-1].startswith("score: ")


def test_text_output_names_the_file(capsys):
    _, out, _ = run([str(FIXTURES / "en_slop.md")], capsys)
    assert out.splitlines()[0].startswith(f"{FIXTURES / 'en_slop.md'}:1:1 ")


def test_code_and_urls_are_never_flagged(capsys):
    code, out, _ = run(["--format", "json", str(FIXTURES / "code_urls.md")], capsys)
    data = json.loads(out)
    assert data["findings"] == []
    assert code == 0


def test_em_dash_is_dropped_on_cyrillic_lines_only(capsys, monkeypatch):
    text = "Use the queue — it is safer.\n\nОчередь — это надёжнее.\n"
    _, data, _ = run_json([], capsys, text, monkeypatch)
    dashes = [f for f in data["findings"] if f["rule_id"] == "ai-tells.EmDashUsage"]
    assert [f["line"] for f in dashes] == [1]


def test_english_text_does_not_run_aiwru(capsys, monkeypatch, fake_engines):
    _, data, _ = run_json([], capsys, "Plain English text.\n", monkeypatch)
    assert logged(fake_engines, "aiwru.log") == []
    assert data["engines"]["aiw-ru"]["status"] == "skipped"


def test_mixed_text_routes_paragraphs(capsys, monkeypatch, fake_engines):
    text = "This is robust.\n\nВ современном мире всё иначе — robust.\n"
    _, data, _ = run_json([], capsys, text, monkeypatch)
    calls = logged(fake_engines, "aiwru.log")
    assert len(calls) == 1
    sent = calls[0]["text"]
    assert len(sent) == len(text)
    assert "This is robust" not in sent and "В современном мире" in sent
    rules = [(f["rule_id"], f["line"]) for f in data["findings"]]
    # ai-tells keeps an English match even inside the Russian paragraph, but never its
    # punctuation rules there; aiw-ru reports the Russian phrase.
    assert ("ai-tells.OverusedVocabulary", 1) in rules
    assert ("ai-tells.OverusedVocabulary", 3) in rules
    assert ("ai-tells.EmDashUsage", 3) not in rules
    ru = next(f for f in data["findings"] if f["engine"] == "aiw-ru")
    assert ru["rule_id"] == "aiwru.tier1.в-современном-мире"
    assert ru["severity"] == "warning"
    assert ru["suggestion"] == "убрать и начать с сути"
    assert text[ru["span"][0]:ru["span"][1]] == "В современном мире"
    assert data["engines"]["aiw-ru"]["status"] == "ok"


def test_aiwru_style_only_findings_are_dropped(capsys, monkeypatch):
    _, data, _ = run_json([], capsys, "Данный подход работает.\n", monkeypatch)
    assert [f for f in data["findings"] if f["engine"] == "aiw-ru"] == []


def test_lang_en_skips_aiwru_and_lang_ru_drops_ai_tells(capsys, monkeypatch, fake_engines):
    text = "В современном мире robust.\n"
    _, en, _ = run_json(["--lang", "en"], capsys, text, monkeypatch)
    assert {f["engine"] for f in en["findings"]} == {"vale"}
    _, ru, _ = run_json(["--lang", "ru"], capsys, "Hello robust. Надеюсь, это поможет\n", monkeypatch)
    assert not [f for f in ru["findings"] if f["rule_id"].startswith("ai-tells.")]
    assert any(f["engine"] == "aiw-ru" for f in ru["findings"])


@pytest.mark.parametrize("context, ext, aiw_context", [
    (None, ".md", "general"),
    ("issue", ".md", "technical"),
    ("docs", ".md", "docs"),
    ("tg", ".tg", "telegram"),
    ("pr", ".pr", "technical"),
])
def test_context_maps_to_vale_extension_and_aiwru_context(capsys, monkeypatch, fake_engines, context, ext, aiw_context):
    argv = ["--context", context] if context else []
    run(argv, capsys, "В современном мире.\n", monkeypatch)
    (vale_args,) = [a for a in logged(fake_engines, "vale.log") if a != ["--version"]]
    assert f"--ext={ext}" in vale_args
    assert "--no-global" in vale_args
    aiw_args = logged(fake_engines, "aiwru.log")[-1]["argv"]
    assert aiw_args[aiw_args.index("--context") + 1] == aiw_context


def test_ignore_drops_rules_by_id_or_prefix(capsys, monkeypatch):
    text = "Great question, robust.\n"
    code, data, _ = run_json(["--ignore", "TextLint.Sycophancy", "--ignore", "ai-tells"], capsys, text, monkeypatch)
    assert data["findings"] == []
    assert code == 0


def test_missing_vale_exits_2_and_names_install(capsys, monkeypatch):
    monkeypatch.setenv("SLOP_CHECK_VALE", "/nonexistent/vale")
    code, _, err = run([], capsys, "text\n", monkeypatch)
    assert code == 2
    assert "vale" in err and "brew install vale" in err
    assert "SLOP_CHECK_VALE=/nonexistent/vale is not an executable file" in err


def test_missing_aiwru_exits_2_only_when_russian_text_needs_it(capsys, monkeypatch):
    monkeypatch.setenv("SLOP_CHECK_AIWRU", "/nonexistent/aiw-ru")
    code, _, _ = run([], capsys, "English only.\n", monkeypatch)
    assert code == 0
    code, _, err = run([], capsys, "Русский текст.\n", monkeypatch)
    assert code == 2
    assert "aiw-ru" in err and "uv tool install aiw-ru" in err


def test_engine_failure_exits_2(capsys, monkeypatch):
    monkeypatch.setenv("FAKE_VALE_FAIL", "1")
    code, _, err = run([], capsys, "text\n", monkeypatch)
    assert code == 2
    assert "vale" in err and "Runtime error" in err


@pytest.mark.parametrize("engine, output", [
    ("VALE", "[]"),
    ("VALE", '{"stdin.md": [{"Check": "x"}]}'),
    ("VALE", "not json"),
    ("VALE", '{"stdin.md": [{"Check": null, "Line": 1, "Span": [1, 2], "Match": "te", "Severity": "error"}]}'),
    ("AIWRU", "[]"),
    ("AIWRU", '{"issues": [{"type": "x"}]}'),
    # Well-formed JSON pointing outside the text.
    ("VALE", '{"stdin.md": [{"Check": "x", "Line": 0, "Span": [1, 2], "Match": "ab", "Severity": "error"}]}'),
    ("VALE", '{"stdin.md": [{"Check": "x", "Line": 9, "Span": [1, 2], "Match": "ab", "Severity": "error"}]}'),
    ("AIWRU", '{"score": 1, "issues": [{"type": "x", "rule": "y", "severity": "P1", "text": "absent",'
              ' "index": -1, "line": 0, "column": 0, "hint": ""}]}'),
])
def test_malformed_engine_output_exits_2(capsys, monkeypatch, engine, output):
    monkeypatch.setenv(f"FAKE_{engine}_OUTPUT", output)
    code, _, err = run([], capsys, "Русский текст. English text.\n", monkeypatch)
    assert code == 2
    assert ("vale" if engine == "VALE" else "aiw-ru") in err


def test_usage_errors_exit_2(capsys, tmp_path):
    for argv in (["check", "--format", "xml"], [], ["lint"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    code, _, err = run([str(tmp_path / "missing.md")], capsys)
    assert code == 2 and "missing.md" in err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"text-lint {__version__}"


def test_check_reads_a_file_named_like_a_subcommand(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "install-skill").write_text("We utilize caches.\n", encoding="utf-8")
    code, out, _ = run(["install-skill"], capsys)
    assert code == 1 and out.startswith("install-skill:1:4 TextLint.Utilize ")


def test_russian_score_is_aiwru_score_unless_our_rules_find_more(capsys, monkeypatch):
    # The fake aiw-ru scores 60 when it finds anything; no TextLint rule fires here.
    _, data, _ = run_json([], capsys, "В современном мире всё иначе.\n", monkeypatch)
    assert data["score"] == 60


def test_mixed_score_is_word_weighted(capsys, monkeypatch):
    # English: 3 words, one error (3 points) -> capped at 100. Russian: 4 words, aiw-ru 60.
    _, data, _ = run_json([], capsys, "This is robust.\n\nВ современном мире всё.\n", monkeypatch)
    assert data["score"] == round((100 * 3 + 60 * 4) / 7)


def test_text_output_escapes_control_characters():
    from text_lint.cli import _format_text

    report = {"score": 1, "engines": {"laya": {"status": "off"}}, "findings": [{
        "line": 1, "col": 1, "rule_id": "r", "message": "m '\x1b]52;c;aGk=\x07'\nnext",
        "suggestion": "",
    }]}
    first = _format_text(report, "<stdin>").splitlines()[0]
    assert "\x1b" not in first and "\x07" not in first
    assert first == "<stdin>:1:1 r m '\\x1b]52;c;aGk=\\x07' next"


def test_cli_imports_without_posix_only_modules():
    # Windows has no grp/pwd; --laya must not be what breaks every other command there.
    code = ("import sys; sys.modules['grp'] = None; sys.modules['pwd'] = None; "
            "from text_lint.cli import main; main(['--version'])")
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("text-lint ")


def test_whole_line_url_does_not_split_a_paragraph(capsys, monkeypatch, fake_engines):
    text = "Continuing from above, the build is fine:\nhttps://github.com/org/repo/pull/9\nГотово.\n"
    _, data, _ = run_json([], capsys, text, monkeypatch)
    # One English-majority paragraph: aiw-ru reads it whole, nothing masked away.
    assert data["engines"]["aiw-ru"]["status"] == "ok"
    sent = json.loads((fake_engines / "aiwru.log").read_text(encoding="utf-8").splitlines()[-1])["text"]
    assert "Continuing from above" in sent


def test_english_match_in_russian_paragraph_does_not_set_russian_score(capsys, monkeypatch):
    # aiw-ru finds nothing; the English ai-tells hit is reported but is no Russian signal.
    _, data, _ = run_json([], capsys, "Это robust решение тут.\n", monkeypatch)
    assert any(f["rule_id"].startswith("ai-tells.") for f in data["findings"])
    assert data["score"] == 0


def test_described_aiwru_issue_points_at_its_character(capsys, monkeypatch):
    monkeypatch.setenv("FAKE_AIWRU_OUTPUT", '{"score": 10, "issues": [{"type": "typography-chars", '
                       '"rule": "nbsp", "severity": "P2", "text": "1 неразрывный пробел (U+00A0)", '
                       '"index": 1, "line": 1, "column": 2, "hint": ""}]}')
    code, data, _ = run_json([], capsys, "В\u00a0мире.\n", monkeypatch)
    assert code == 1
    assert data["findings"][0]["match"] == "\u00a0" and data["findings"][0]["col"] == 2


def test_dropped_aiwru_findings_stop_scoring(capsys, monkeypatch):
    monkeypatch.setenv("FAKE_AIWRU_OUTPUT", '{"score": 63, "issues": [{"type": "x", "rule": "y", '
                       '"severity": "P1", "text": "мире", "index": 2, "line": 1, "column": 3, "hint": ""}]}')
    _, data, _ = run_json(["--ignore", "aiwru"], capsys, "В мире всё.\n", monkeypatch)
    assert data["findings"] == [] and data["score"] == 0


def test_bundled_aiwru_exe_is_found_on_windows(monkeypatch, tmp_path):
    from text_lint import engines

    exe = tmp_path / "aiw-ru.exe"
    exe.write_text("")
    exe.chmod(0o755)
    monkeypatch.delenv("SLOP_CHECK_AIWRU")
    monkeypatch.setattr(engines.sys, "executable", str(tmp_path / "python.exe"))
    assert engines.find_aiwru() == str(exe)


def test_error_output_escapes_control_characters(capsys):
    code, _, err = run(["missing-\x1b]52;c;aGk=\x07.md"], capsys)
    assert code == 2
    assert "\x1b" not in err and "\x07" not in err and "\\x1b" in err


def _set_home(monkeypatch, path):
    # expanduser reads HOME on POSIX and USERPROFILE on Windows.
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


def test_install_skill_writes_the_blurb_once(capsys, monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path)
    assert main(["install-skill"]) == 0
    # rig reads .blurbs/<tools.items name>.md as the "installed" marker.
    blurb = tmp_path / ".agents" / "skills" / ".blurbs" / "text-lint.md"
    text = blurb.read_text(encoding="utf-8")
    assert text.startswith("- `text-lint`") and text.count("\n") == 1
    assert main(["install-skill"]) == 0
    assert "already current" in capsys.readouterr().out


def test_install_skill_removes_the_0_1_blurb(capsys, monkeypatch, tmp_path):
    # slop-check 0.1 wrote .blurbs/slop-check.md; keeping it would advertise the tool twice.
    _set_home(monkeypatch, tmp_path)
    blurbs = tmp_path / ".agents" / "skills" / ".blurbs"
    blurbs.mkdir(parents=True)
    (blurbs / "slop-check.md").write_text("- `slop-check` old\n", encoding="utf-8")
    (blurbs / "tg.md").write_text("- `tg`\n", encoding="utf-8")
    assert main(["install-skill"]) == 0
    assert sorted(p.name for p in blurbs.iterdir()) == ["text-lint.md", "tg.md"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_install_skill_validates_arguments_and_reports_failures(capsys, monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path)
    blurbs = tmp_path / ".agents" / "skills" / ".blurbs"
    for argv in (["install-skill", "--help"], ["install-skill", "body.md"]):
        with pytest.raises(SystemExit):
            main(argv)
    assert not blurbs.exists()
    blurbs.mkdir(parents=True)
    (blurbs / "text-lint.md").write_bytes(b"\xff\xfe")
    assert main(["install-skill"]) == 0
    (blurbs / "text-lint.md").unlink()
    blurbs.chmod(0o500)
    try:
        code, _, err = invoke(["install-skill"], capsys)
    finally:
        blurbs.chmod(0o700)
    assert code == 2 and "install-skill failed" in err


def test_failed_install_skill_write_leaves_no_marker(capsys, monkeypatch, tmp_path):
    _set_home(monkeypatch, tmp_path)

    def full_disk(self, data):
        self.open("wb").close()
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", full_disk)
    code, _, _ = invoke(["install-skill"], capsys)
    blurbs = tmp_path / ".agents" / "skills" / ".blurbs"
    assert code == 2 and list(blurbs.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="control characters are invalid in Windows paths")
def test_install_skill_output_escapes_control_characters_in_home(capsys, monkeypatch, tmp_path):
    home = tmp_path / "h\x1b]52;c;aGk=\x07"
    _set_home(monkeypatch, home)
    code, out, _ = invoke(["install-skill"], capsys)
    assert code == 0 and "\x1b" not in out and "\x07" not in out


def test_stdin_and_file_give_the_same_spans_for_crlf(capsys, monkeypatch, tmp_path):
    text = "plain\r\nGreat question\r\n"
    path = tmp_path / "crlf.md"
    path.write_bytes(text.encode())
    _, piped, _ = run_json([], capsys, text, monkeypatch)
    _, filed, _ = run_json([str(path)], capsys)
    assert [f["span"] for f in piped["findings"]] == [f["span"] for f in filed["findings"]] == [[6, 20]]
