"""`slop-check`, the 0.1 command name, kept as a shim that runs `text-lint`."""

import pytest

from text_lint import __version__, shim

# tests/conftest.py: fake vale and aiw-ru for every test here.
pytestmark = pytest.mark.usefixtures("fake_engines")

PREFIX = ["check", "--profile", "prose", "--fail-on", "suggestion"]


def test_shim_maps_context():
    assert shim.translate(["--context", "tg", "-"]) == ["check", "--profile", "tg-bot", "--fail-on", "suggestion", "-"]
    assert shim.translate(["--context", "docs", "--lang", "ru", "-"]) == [*PREFIX, "--lang", "ru", "-"]
    assert shim.translate(["--context=issue", "x.md"])[:3] == ["check", "--profile", "issue"]
    assert shim.translate(["--cont", "pr"])[:3] == ["check", "--profile", "pr"]
    # `echo text | slop-check`: no arguments reads stdin.
    assert shim.translate([]) == PREFIX


def test_shim_drops_laya():
    # Laya is the profile's choice now; 0.1's switch (and its abbreviation) is ignored.
    assert shim.translate(["--laya", "--format", "json", "-"]) == [*PREFIX, "--format", "json", "-"]
    assert shim.translate(["--lay", "x.md"]) == [*PREFIX, "x.md"]


def test_shim_flag_with_a_value_is_still_an_error(capsys):
    # 0.1's argparse rejected `--laya=1` and `--version=1`; they reach text-lint's parser as is.
    assert shim.translate(["--laya=1", "x.md"]) == [*PREFIX, "--laya=1", "x.md"]
    for argv in (["--laya=1", "x.md"], ["--version=1"]):
        with pytest.raises(SystemExit) as exc:
            shim.main(argv)
        assert exc.value.code == 2


def test_shim_maps_ignore_prefix():
    assert shim.translate(["--ignore", "SlopCheck.Hedging"]) == [*PREFIX, "--ignore", "TextLint.Hedging"]
    assert shim.translate(["--ignore=SlopCheck.Hedging", "--ignore", "SlopCheck"]) == [
        *PREFIX, "--ignore=TextLint.Hedging", "--ignore", "TextLint"]
    # Only an --ignore value is a rule id; a file may be named anything.
    assert shim.translate(["SlopCheck.Hedging"]) == [*PREFIX, "SlopCheck.Hedging"]


def test_shim_follows_argparse_abbreviations():
    # slop-check 0.1 parsed with argparse, which takes any unique prefix of an option. They
    # are spelled out: `--i` and `--f` would be ambiguous in text-lint (--input-json, --fail-on).
    assert shim.translate(["--ign", "SlopCheck.Hedging", "--i=SlopCheck", "--f", "json"]) == [
        *PREFIX, "--ignore", "TextLint.Hedging", "--ignore=TextLint", "--format", "json"]
    assert shim.translate(["--vers"]) == ["--version"]
    # After `--` every argument is a path, as in argparse.
    assert shim.translate(["--", "--version"]) == [*PREFIX, "--", "--version"]
    assert shim.translate(["--", "--ignore", "SlopCheck.X"]) == [*PREFIX, "--", "--ignore", "SlopCheck.X"]


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
