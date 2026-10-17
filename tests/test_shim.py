"""`slop-check`, the 0.1 command name, kept as a shim that runs `text-lint`."""

import pytest

from text_lint import __version__, shim

# tests/conftest.py: fake vale and aiw-ru for every test here.
pytestmark = pytest.mark.usefixtures("fake_engines")


def test_shim_passes_legacy_args():
    assert shim.translate(["--context", "tg", "--lang", "ru", "-"]) == [
        "check", "--context", "tg", "--lang", "ru", "-"]
    # `echo text | slop-check`: no arguments reads stdin.
    assert shim.translate([]) == ["check"]


def test_shim_maps_ignore_prefix():
    assert shim.translate(["--ignore", "SlopCheck.Hedging"]) == ["check", "--ignore", "TextLint.Hedging"]
    assert shim.translate(["--ignore=SlopCheck.Hedging", "--ignore", "SlopCheck"]) == [
        "check", "--ignore=TextLint.Hedging", "--ignore", "TextLint"]
    # Only an --ignore value is a rule id; a file may be named anything.
    assert shim.translate(["SlopCheck.Hedging"]) == ["check", "SlopCheck.Hedging"]


def test_shim_follows_argparse_abbreviations():
    # slop-check 0.1 parsed with argparse, which takes any unique prefix of an option.
    assert shim.translate(["--ign", "SlopCheck.Hedging", "--i=SlopCheck"]) == [
        "check", "--ign", "TextLint.Hedging", "--i=TextLint"]
    assert shim.translate(["--vers"]) == ["--version"]
    # After `--` every argument is a path, as in argparse.
    assert shim.translate(["--", "--version"]) == ["check", "--", "--version"]
    assert shim.translate(["--", "--ignore", "SlopCheck.X"]) == ["check", "--", "--ignore", "SlopCheck.X"]


def test_shim_version_names_slop_check(capsys):
    assert shim.translate(["--version"]) == ["--version"]
    assert shim.main(["--version"]) == 0
    assert capsys.readouterr().out == f"slop-check {__version__}\n"


def test_shim_install_skill(capsys, monkeypatch, tmp_path):
    assert shim.translate(["install-skill"]) == ["install-skill"]
    # install.sh (lines 42 and 169 in 0.1) runs `slop-check install-skill`.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert shim.main(["install-skill"]) == 0
    assert [p.name for p in (tmp_path / ".agents" / "skills" / ".blurbs").glob("*.md")] == ["text-lint.md"]


def test_shim_prints_deprecation(capsys, monkeypatch, tmp_path):
    path = tmp_path / "clean.md"
    path.write_text("The importer reads one file per run.\n", encoding="utf-8")
    assert shim.main([str(path)]) == 0
    assert "slop-check is deprecated; use text-lint check" in capsys.readouterr().err


def test_shim_runs_real_cli(capsys, tmp_path):
    path = tmp_path / "praise.md"
    path.write_text("Great catch, the cache key was wrong.\n", encoding="utf-8")
    assert shim.main(["--context", "tg", str(path)]) == 1
    assert "TextLint.Sycophancy" in capsys.readouterr().out
