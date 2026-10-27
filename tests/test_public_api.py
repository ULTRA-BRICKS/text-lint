import json

import pytest

import text_lint


def document(text, **meta):
    return {"schema": "text-lint/input@1", "fields": [
        {"name": "body", "text": text, "format": "plain"}], "meta": meta}


def test_public_report(fake_engines, laya_server):
    report = text_lint.check(document("Hello from the test.", agent_authored=True), profile="tg-bot")
    assert report["schema"] == "text-lint/report@1"
    assert report["engines"]["laya"]["status"] == "ok"
    assert report["intent"]["action"] == "pass"


@pytest.mark.parametrize("option,value", [("profile", "missing"), ("lang", "xx"),
    ("enforce", "off"), ("laya_mode", "off"), ("laya_mode", "ci")])
def test_bad_options_are_public_errors(option, value, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(text_lint.TextLintError):
        text_lint.check("hello", **{option: value})


@pytest.mark.parametrize("value", [None, 42, {}, {"schema": "text-lint/input@1", "fields": [] , "meta": {"automated": 1}}, "\ud800"])
def test_bad_input_is_public_error(value):
    with pytest.raises(text_lint.TextLintError):
        text_lint.check(value)


def test_dictionary_error_names_path(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{")
    with pytest.raises(text_lint.TextLintError, match="broken.json"):
        text_lint.check("Hello", dict_path=path)


def test_explicit_socket_never_starts(fake_engines, monkeypatch, tmp_path):
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(tmp_path / "absent.sock"))
    with pytest.raises(text_lint.TextLintError, match="Laya"):
        text_lint.check(document("Hello"), profile="tg-bot")
    report = text_lint.check(document("Hello", automated=True), profile="tg-bot")
    assert report["engines"]["laya"]["status"] == "unreachable"
    assert not (tmp_path / "task.log").exists()


@pytest.mark.parametrize("profile", ["as-user", "spoken"])
def test_identity_profiles(profile, fake_engines, laya_server):
    laya_server.answers["first_person"] = .9
    report = text_lint.check("Done ✅", profile=profile)
    assert report["verdict"] == "block"
    ids = {f["rule_id"] for f in report["findings"]}
    assert "AsUser.Emoji" in ids
    if profile == "spoken":
        assert "Spoken.Markup" in ids


@pytest.mark.parametrize("text", ["<b>Hello</b>", "## Results\n- item", "**hello**", "`code`", "[REPORT] done"])
def test_bot_formatting(text, fake_engines, laya_server):
    laya_server.answers["first_person"] = .9
    report = text_lint.check(text, profile="as-user")
    assert any(f["rule_id"] == "AsUser.BotFormatting" for f in report["findings"])


def test_first_person_answer_required(fake_engines, laya_server):
    laya_server.answers["first_person"] = .3
    assert text_lint.check("I wrote it", profile="as-user")["verdict"] == "block"
    laya_server.raw_reply = json.dumps({"ok": True, "result": {"answers": {"slop": {"type": "noul", "noul": .1}}}}).encode() + b"\n"
    with pytest.raises(text_lint.TextLintError, match="first_person"):
        text_lint.check("I wrote it", profile="as-user")


def test_missing_pack_refuses(fake_engines, monkeypatch, tmp_path):
    from text_lint.engines import native
    monkeypatch.setattr(native, "PACK_DIR", tmp_path)
    with pytest.raises(text_lint.TextLintError, match="pack"):
        text_lint.check("Hello", profile="as-user")


def test_public_function_survives_internal_imports(fake_engines, laya_server):
    import importlib
    importlib.import_module("text_lint.check")
    importlib.import_module("text_lint.cli")
    assert callable(text_lint.check)
    text_lint.check("Hello", profile="tg-bot")


@pytest.mark.parametrize("text", ["I counted 123 items.", "I use ♭ in music.", "I read 🄀 as a digit."])
def test_non_emoji_symbols_pass(text, fake_engines, laya_server):
    laya_server.answers["first_person"] = .9
    report = text_lint.check(text, profile="as-user")
    assert not any(f["rule_id"] == "AsUser.Emoji" for f in report["findings"])


def test_spoken_raw_fields_and_ungated_identity(fake_engines, laya_server):
    laya_server.answers["first_person"] = .9
    doc = document("hello")
    doc["fields"] += [{"name": "harness_title", "text": "✅", "format": "plain", "gate": False}]
    report = text_lint.check(doc, profile="spoken")
    assert report["verdict"] == "warn"
    doc["fields"][0].update(text="<b>Hello</b> https://example.com", format="html")
    report = text_lint.check(doc, profile="spoken")
    assert {f["rule_id"] for f in report["findings"]} >= {"AsUser.BotFormatting", "Spoken.Markup"}


@pytest.mark.parametrize("profile", ["tg-bot", "as-user", "spoken"])
def test_missing_mandatory_answer_is_not_ok(profile, fake_engines, laya_server):
    laya_server.raw_reply = b'{"ok":true,"result":{"answers":{}}}\n'
    with pytest.raises(text_lint.TextLintError, match="missing required"):
        text_lint.check("Hello", profile=profile)
    report = text_lint.check(document("Hello", automated=True), profile=profile)
    assert report["engines"]["laya"]["status"] == "unreachable"
