"""DICT.json: where it is found, how it loads as `Dict.*` pattern rules, and how a broken or
missing file ends a run (exit 2, naming the file) or switches the rules off."""

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from text_lint import profiles
from text_lint.check import check_units
from text_lint.cli import main
from text_lint.inputs import Meta, unit_from_text
from text_lint.native import dictionary
from text_lint.profiles import ConfigError, load_profile

PARITY = Path(__file__).resolve().parent / "parity"
TG_DICT = PARITY / "tg" / "DICT.json"
REAL_DICT = Path("~/.claude/DICT.json").expanduser()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def dict_file(tmp_path, rules, name="DICT.json", **extra):
    return write(tmp_path / name, json.dumps({"version": 1, "rules": rules, **extra}, ensure_ascii=False))


POCHTIT = {"id": "pochtit", "pattern": r"\bпочтил\b", "flags": "iu", "replacement": "починил", "why": "no such verb"}


def test_dict_resolution(tmp_path):
    a = write(tmp_path / "a.json", "{}")
    b = write(tmp_path / "b.json", "{}")
    c = write(tmp_path / "c.json", "{}")
    home = tmp_path / "home"
    default = write(home / ".claude" / "DICT.json", "{}")
    env = {"TEXT_LINT_DICT": str(a), "TG_DICT_PATH": str(b), "HOME": str(home)}
    assert dictionary.resolve_path(str(c), env) == (c, True)
    assert dictionary.resolve_path(None, env) == (a, True)
    assert dictionary.resolve_path(None, {**env, "TEXT_LINT_DICT": ""}) == (b, True)
    assert dictionary.resolve_path(None, {"HOME": str(home)}) == (default, False)
    assert dictionary.resolve_path(None, {"HOME": str(tmp_path / "nobody")}) is None
    assert dictionary.resolve_path(None, {}) is None
    for cli, env, source in [(str(tmp_path / "gone.json"), {}, "--dict"),
                             (None, {"TEXT_LINT_DICT": str(tmp_path / "gone.json")}, "TEXT_LINT_DICT"),
                             (None, {"TG_DICT_PATH": str(tmp_path / "gone.json")}, "TG_DICT_PATH")]:
        with pytest.raises(ConfigError) as exc:
            dictionary.resolve_path(cli, env)
        assert str(tmp_path / "gone.json") in str(exc.value) and source in str(exc.value)


def test_load_real_shape_as_dict_rules():
    raw = json.loads(TG_DICT.read_text(encoding="utf-8"))["rules"]
    rules = dictionary.load(TG_DICT, True)
    assert [r.id for r in rules] == [f"Dict.{r['id']}" for r in raw]
    assert [r.replacement for r in rules] == [r["replacement"] for r in raw]
    assert [r.why for r in rules] == [r["why"] for r in raw]
    assert {r.kind for r in rules} == {"pattern"}
    assert {r.severity for r in rules} == {"error"}
    assert all(r.profiles == profiles.names() for r in rules)
    assert all(r.examples == {} for r in rules)
    land = next(r for r in rules if r.id == "Dict.land-landing-in-russian")
    assert land.params == {"pattern": r"\bland(?:ing|ed|s)?\b", "flags": "iu", "only_if_script": "cyrillic"}
    pochtit = rules[0]
    assert "only_if_script" not in pochtit.params


def test_missing_file_at_load(tmp_path):
    assert dictionary.load(tmp_path / "gone.json", False) == []
    with pytest.raises(ConfigError, match="gone.json"):
        dictionary.load(tmp_path / "gone.json", True)


def test_malformed_dict_names_file(tmp_path, capsys, monkeypatch):
    path = write(tmp_path / "broken" / "DICT.json", "{ not json")
    with pytest.raises(ConfigError) as exc:
        dictionary.load(path, True)
    assert str(path) in str(exc.value) and "JSON" in str(exc.value)
    # The tg-cli case: TG_DICT_PATH set in ~/.config/tg-cli/.env names a broken file.
    monkeypatch.delenv("TEXT_LINT_DICT", raising=False)
    monkeypatch.setenv("TG_DICT_PATH", str(path))
    monkeypatch.setattr("sys.stdin", io.StringIO("Всё готово.\n"))
    code = main(["check", "--profile", "chat"])
    out, err = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert str(path) in err
    # A broken default file fails closed too: only a missing one switches the rules off.
    monkeypatch.delenv("TG_DICT_PATH")
    home = tmp_path / "home"
    write(home / ".claude" / "DICT.json", "{ not json")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin", io.StringIO("Всё готово.\n"))
    assert main(["check", "--profile", "chat"]) == 2
    assert str(home / ".claude" / "DICT.json") in capsys.readouterr().err


@pytest.mark.parametrize("data, problems", [
    ({"version": 2, "rules": []}, ["version"]),
    ({"version": 1}, ['"rules"']),
    ([], ['"rules"']),
    ({"rules": [5]}, ["#1", "not an object"]),
    ({"rules": [{"id": "no-pattern", "replacement": "x"}]}, ['"no-pattern"', "pattern"]),
    ({"rules": [{"id": "", "pattern": "a", "replacement": "x"}]}, ["#1", "id"]),
    ({"rules": [{"id": "r", "pattern": "a", "replacement": 1}]}, ['"r"', "replacement"]),
    ({"rules": [dict(POCHTIT, flags=5)]}, ['"pochtit"', "flags"]),
    ({"rules": [dict(POCHTIT, flags="ix")]}, ['"pochtit"', "unknown flag"]),
    ({"rules": [dict(POCHTIT, only_if_cyrillic="yes")]}, ['"pochtit"', "only_if_cyrillic"]),
    ({"rules": [POCHTIT, dict(POCHTIT, id="broken", pattern="(unclosed")]}, ['"broken"', "invalid regex"]),
    ({"rules": [dict(POCHTIT, pattern="\bпочтил\b")]}, ['"pochtit"', "U+0008", "backspace"]),
])
def test_invalid_dictionary_names_file_and_rule(tmp_path, data, problems):
    path = write(tmp_path / "DICT.json", json.dumps(data, ensure_ascii=False))
    with pytest.raises(ConfigError) as exc:
        dictionary.load(path, False)
    message = str(exc.value)
    assert str(path) in message
    for problem in problems:
        assert problem in message


def test_lenient_cases_load(tmp_path):
    bom = write(tmp_path / "bom.json", "\ufeff" + json.dumps({"version": 1, "rules": [POCHTIT]}))
    assert [r.id for r in dictionary.load(bom, True)] == ["Dict.pochtit"]
    no_version = write(tmp_path / "nv.json", json.dumps({"rules": []}))
    assert dictionary.load(no_version, True) == []
    no_why = {k: v for k, v in POCHTIT.items() if k != "why"}
    (r,) = dictionary.load(dict_file(tmp_path, [dict(no_why, replacement="")], name="nowhy.json"), True)
    assert (r.why, r.replacement) == (None, "")
    # tg-cli never reads "why", so a non-string one is ignored, not an error.
    (r,) = dictionary.load(dict_file(tmp_path, [dict(POCHTIT, why=3)], name="why3.json"), True)
    assert (r.why, r.message) == (None, "Banned wording '{match}'")


def run_check(argv, capsys, monkeypatch, stdin):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["check", "--format", "json", *argv])
    out, err = capsys.readouterr()
    return code, json.loads(out) if out else None, err


def test_dict_flag_wires_dict_rules_into_check(tmp_path, capsys, monkeypatch):
    code, data, _ = run_check(["--profile", "chat", "--dict", str(TG_DICT)], capsys, monkeypatch,
                              "Я почтил баг.\n")
    assert code == 1
    (finding,) = data["findings"]
    assert finding == {
        "rule_id": "Dict.pochtit-as-fix", "engine": "native", "severity": "error", "line": 1, "col": 3,
        "span": [2, 8], "match": "почтил",
        "message": "Banned wording 'почтил': There is no verb 'почтить' meaning 'to fix'. Ban is by stem, not by "
                   "a list of forms — three earlier list-based bans leaked new forms (почтил, почтётся).",
        "suggestion": "починил / починен / патчил / чинится / будет починено / применён",
        "field": None, "path": None, "key": None}
    native = data["engines"]["native"]
    assert (native["status"], native["dict"]) == ("ok", str(TG_DICT))
    assert native["rules"] >= 13
    # --dict wins over the environment.
    monkeypatch.setenv("TEXT_LINT_DICT", str(tmp_path / "gone.json"))
    code, data, _ = run_check(["--profile", "chat", "--dict", str(TG_DICT)], capsys, monkeypatch, "Я почтил баг.\n")
    assert code == 1
    code, _, err = run_check(["--profile", "chat"], capsys, monkeypatch, "Я почтил баг.\n")
    assert code == 2 and "TEXT_LINT_DICT" in err and "gone.json" in err


def test_text_output_shows_replacement(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Я почтил баг.\n"))
    assert main(["check", "--profile", "chat", "--dict", str(TG_DICT)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("<stdin>:1:3 Dict.pochtit-as-fix Banned wording 'почтил'")
    assert "→ починил / починен" in out


def test_default_missing_means_no_dict_rules(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TEXT_LINT_DICT", raising=False)
    monkeypatch.delenv("TG_DICT_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    code, data, err = run_check(["--profile", "chat"], capsys, monkeypatch, "Я почтил баг.\n")
    assert (code, data["findings"], err) == (0, [], "")
    # Only the script pack's rules ran.
    native = data["engines"]["native"]
    assert (native["status"], native["rules"], native["dict"]) == ("ok", 2, None)


def test_dict_rules_only_for_profiles_listing_dict():
    profile = load_profile("chat")
    units = [unit_from_text("Я почтил баг.", "plain")]
    with_dict = check_units(units, Meta(), profile, enforce="block", dict_path=str(TG_DICT))
    assert [f["rule_id"] for f in with_dict["findings"]] == ["Dict.pochtit-as-fix"]
    without = check_units(units, Meta(), replace(profile, packs=[]), enforce="block", dict_path=str(TG_DICT))
    assert without["findings"] == []
    assert without["verdict"] == "pass"


def test_native_cap_and_ignore_apply_to_dict_rules():
    profile = load_profile("chat")
    capped = replace(profile, engines={**profile.engines,
                                       "native": replace(profile.engines["native"], cap="warning")})
    units = [unit_from_text("Я почтил баг.", "plain")]
    report = check_units(units, Meta(), capped, enforce="block", dict_path=str(TG_DICT))
    assert [f["severity"] for f in report["findings"]] == ["warning"]
    report = check_units(units, Meta(), profile, enforce="block", dict_path=str(TG_DICT), ignore=["Dict"])
    assert report["findings"] == []


def test_dict_findings_count_toward_the_score():
    # The hit is in the Russian body (3 words), not the English title (10 words): Russian
    # density min(100, 3 points * 1000 / 3 words) = 100, English 0, word-weighted 300 / 13 = 23.
    # Counted in the title's paragraph it would be 1000 / 13 = 77.
    units = [unit_from_text("The parser reads one file per run and stops today.", "plain", field="title"),
             unit_from_text("<b>Я</b> почтил баг", "html", field="body")]
    report = check_units(units, Meta(), load_profile("chat"), enforce="block", dict_path=str(TG_DICT))
    assert [f["rule_id"] for f in report["findings"]] == ["Dict.pochtit-as-fix"]
    assert report["score"] == 23
    ignored = check_units(units, Meta(), load_profile("chat"), enforce="block", dict_path=str(TG_DICT),
                          ignore=["Dict"])
    assert ignored["score"] == 0


@pytest.mark.skipif(not REAL_DICT.exists(), reason="no personal ~/.claude/DICT.json on this machine")
def test_real_dictionary_loads():
    raw = json.loads(REAL_DICT.read_text(encoding="utf-8-sig"))
    assert len(dictionary.load(REAL_DICT, True)) == len(raw["rules"])
