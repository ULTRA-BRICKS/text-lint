"""Deeply nested JSON inside the reply cap is a malformed listener, not a crash."""
import pytest

import text_lint
from text_lint.engines import laya


def document(text, **meta):
    return {"schema": "text-lint/input@1", "fields": [
        {"name": "body", "text": text, "format": "plain"}], "meta": meta}


def nested(kind, depth):
    if kind == "array":
        return "[" * depth + "]" * depth
    return '{"a":' * depth + "0" + "}" * depth


def reply(kind, depth, size=None):
    line = '{"ok":true,"result":{"answers":{"slop":{"noul":0.1}},"x":' + nested(kind, depth) + "}}"
    pad = 0 if size is None else size - 1 - len(line)
    assert pad >= 0
    return (line + " " * pad + "\n").encode()


@pytest.mark.parametrize("kind", ["array", "object"])
@pytest.mark.parametrize("depth", [10000, 20000])
def test_nested_reply_is_unavailable_not_absent(kind, depth, fake_engines, laya_server):
    laya_server.raw_reply = reply(kind, depth)
    with pytest.raises(laya.LayaUnavailable) as error:
        laya.ask("Hello", ["slop"], start=False)
    assert not isinstance(error.value, laya.LayaNotListening)
    with pytest.raises(text_lint.TextLintError, match="Laya"):
        text_lint.check("Hello", profile="tg-bot")
    report = text_lint.check(document("Hello", automated=True), profile="tg-bot")
    assert report["engines"]["laya"]["status"] == "unreachable"
    assert report["verdict"] in {"pass", "warn", "block"}


def test_nested_reply_at_the_cap_then_valid_request(fake_engines, laya_server):
    laya_server.raw_reply = reply("array", 20000, size=laya._MAX_REPLY_BYTES)
    with pytest.raises(laya.LayaUnavailable):
        laya.ask("Hello", ["slop"], start=False)
    laya_server.raw_reply = None
    assert laya.ask("Hello", ["slop"], start=False)["answers"] == {"slop": 0.1}
    assert text_lint.check("Hello", profile="tg-bot")["engines"]["laya"]["status"] == "ok"
