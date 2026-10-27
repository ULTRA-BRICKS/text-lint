"""Profile files: every shipped profile loads, with the values of spec section 4.5."""

from pathlib import Path

import pytest

from text_lint import profiles
from text_lint.profiles import ConfigError, load_profile

PROFILE_DIR = Path(__file__).resolve().parent.parent / "text_lint" / "profiles"
NAMES = {"prose", "issue", "pr", "commit", "tg-bot", "as-user", "chat", "spoken", "ui-strings"}
# Every profile requires Laya (Main relaying the owner, 2026-10-17).
LAYA_PROFILES = NAMES


def test_profiles_load():
    files = {p.stem for p in PROFILE_DIR.glob("*.yaml")}
    assert files == NAMES == set(profiles.names())
    for name in files:
        assert load_profile(name).name == name
    tg = load_profile("tg-bot")
    assert tg.engines["vale"].cap == "warning"
    assert tg.engines["vale"].optional is True
    assert tg.engines["laya"].optional is False
    assert "first_person" in load_profile("as-user").engines["laya"].options["questions"]
    assert load_profile("commit").engines["laya"].options["questions"] == ["slop"]
    with pytest.raises(ConfigError):
        load_profile("nope")


def test_profile_table():
    fail_on = {name: load_profile(name).fail_on for name in NAMES}
    assert fail_on == {**{name: "warning" for name in NAMES},
                       "tg-bot": "error", "as-user": "error", "chat": "error", "spoken": "error"}
    assert {name for name in NAMES if load_profile(name).engines["laya"].enabled} == LAYA_PROFILES
    for name in LAYA_PROFILES:
        laya = load_profile(name).engines["laya"]
        assert laya.optional is False
        assert laya.options["timeout_ms"] == 1500
        assert laya.options["start_wait_ms"] == 60000
        assert laya.options["max_chars"] > 0
    assert load_profile("tg-bot").engines["laya"].options["questions"] == ["asks_reader", "slop"]
    assert load_profile("issue").engines["laya"].options["questions"] == ["slop"]
    assert load_profile("pr").engines["laya"].options["questions"] == ["slop"]
    # Missing Vale or aiw-ru is a skip, and their findings are capped, only in the send profiles.
    for name in NAMES:
        profile = load_profile(name)
        send = name in {"tg-bot", "as-user", "spoken"}
        for engine in ("vale", "aiw-ru"):
            spec = profile.engines[engine]
            if spec.enabled:
                assert spec.optional is send and spec.cap == ("warning" if send else None), (name, engine)
    chat = load_profile("chat")
    assert not chat.engines["vale"].enabled and not chat.engines["aiw-ru"].enabled
    assert chat.engines["native"].enabled
    assert load_profile("tg-bot").engines["vale"].options["disable"] == ["TextLint.DoneEmoji"]
    exts = {name: load_profile(name).engines["vale"].options.get("ext") for name in NAMES}
    assert exts == {"prose": ".md", "issue": ".md", "pr": ".pr", "commit": ".commit",
                    "tg-bot": ".tg", "as-user": ".tg", "spoken": ".tg", "chat": None,
                    "ui-strings": ".ui"}
    contexts = {name: load_profile(name).engines["aiw-ru"].options.get("context") for name in NAMES}
    assert contexts == {"prose": "docs", "issue": "technical", "pr": "docs", "commit": "technical",
                        "tg-bot": "telegram", "as-user": "chat", "spoken": "chat", "chat": None,
                        "ui-strings": "chat"}


@pytest.mark.parametrize("body, problem", [
    ("name: other\nfail_on: warning\nmarkup: plain\npacks: []\nengines: {}\n", "name"),
    ("name: bad\nfail_on: fatal\nmarkup: plain\npacks: []\nengines: {}\n", "fail_on"),
    ("name: bad\nfail_on: warning\nmarkup: rtf\npacks: []\nengines: {}\n", "markup"),
    ("name: bad\nfail_on: warning\nmarkup: plain\npacks: []\nengines: {grammarly: {enabled: true}}\n",
     "grammarly"),
    ("name: bad\nfail_on: warning\nmarkup: plain\npacks: []\nengines: {vale: {enabled: true, cap: huge}}\n",
     "cap"),
    (("name: bad\nfail_on: warning\nmarkup: plain\npacks: []\n"
     "engines: {laya: {enabled: true, options: {questions: [vibes], max_chars: 1, timeout_ms: 1,"
     " start_wait_ms: 1}}}\n"), "vibes"),
    (("name: bad\nfail_on: warning\nmarkup: plain\npacks: []\n"
     "engines: {laya: {enabled: true, options: {questions: [slop]}}}\n"), "timeout_ms"),
    ("name: bad\nfail_on: warning\nmarkup: plain\npacks: []\nengines: {}\nextra: 1\n", "extra"),
    ("name: [bad\n", "bad.yaml"),
])
def test_invalid_profile_names_the_problem(monkeypatch, tmp_path, body, problem):
    (tmp_path / "bad.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setattr(profiles, "PROFILE_DIR", tmp_path)
    with pytest.raises(ConfigError) as exc:
        load_profile("bad")
    assert problem in str(exc.value)
