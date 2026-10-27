"""The intent classifier (Intent.*, report["intent"]): a port of tg-cli's classifyIntent, run
on the message unit of agent-authored or automated messages, with Laya's `asks_reader`
answer as an advisory hint."""

import pytest
from messages import COMPLIANT, PROBLEM_OK, REPORT_OK, input_doc

from text_lint.check import check_units
from text_lint.engines import native as native_engine
from text_lint.inputs import Meta, parse_input_json, unit_from_text
from text_lint.native import PACK_DIR, intent, load_pack
from text_lint.profiles import load_profile

INTENT_RULES = load_pack(PACK_DIR / "intent")
BARE = "Should we deploy today? Which region should we use?"


def run(profile, doc, **kw):
    units, meta = parse_input_json(doc)
    return check_units(units, meta, load_profile(profile), enforce="block", from_input_json=True, **kw)


def test_bare_questions_block():
    verdict = intent.classify(BARE, Meta(agent_authored=True), None)
    assert verdict.action == "block" and verdict.reason == "malformed-decision"
    assert verdict.kinds == ["decision"]
    (hit,) = native_engine.run([unit_from_text(BARE, "plain")], Meta(agent_authored=True), INTENT_RULES)
    assert hit.rule_id == "Intent.MalformedDecision" and hit.severity == "error"
    assert "«Should we deploy today?»" in hit.message
    assert "It asks 2 separate questions" in hit.message


def test_automated_still_blocks_bare_question():
    assert intent.classify(BARE, Meta(automated=True), None).action == "block"
    hits = native_engine.run([unit_from_text(BARE, "plain")], Meta(automated=True), INTENT_RULES)
    assert [f.rule_id for f in hits] == ["Intent.MalformedDecision"]


def test_person_is_not_classified():
    assert native_engine.run([unit_from_text(BARE, "plain")], Meta(), INTENT_RULES) == []


def test_intent_follow_up():
    body = f"{REPORT_OK} Мержить #694 сейчас? Удалять ветку после мержа?"
    verdict = intent.classify(body, Meta(tag="report", agent_authored=True), None)
    assert verdict.action == "follow-up"
    assert [(f.kind, f.quote) for f in verdict.follow_ups] == [
        ("decision", "Мержить #694 сейчас?"), ("decision", "Удалять ветку после мержа?")]
    assert verdict.valid is None


def test_intent_valid_problem():
    verdict = intent.classify(PROBLEM_OK, Meta(tag="problem", agent_authored=True), None)
    assert (verdict.action, verdict.valid) == ("pass", "problem")
    verdict = intent.classify(PROBLEM_OK, Meta(tag="problem", automated=True), None)
    assert (verdict.action, verdict.valid) == ("pass", None)


def test_mixed_and_tagged_findings():
    mixed = "Смержил #690, тесты зелёные. Блокер: нет доступа к проду. Мержить #694 или ждать?"
    hits = native_engine.run([unit_from_text(mixed, "plain")], Meta(agent_authored=True), INTENT_RULES)
    assert [f.rule_id for f in hits] == ["Intent.Mixed"]
    # A tagged message's own format rule reports it (Format.*): no second finding, as tg-cli
    # never prints that checklist twice.
    hits = native_engine.run([unit_from_text(BARE, "plain")], Meta(tag="decision", agent_authored=True),
                             INTENT_RULES)
    assert hits == []


def test_laya_hint_only_when_no_ask():
    meta = Meta(agent_authored=True)
    verdict = intent.classify(REPORT_OK, meta, 0.95)
    assert verdict.action == "pass"
    assert "laya reads this message as asking the reader something (p=0.95)" in verdict.hints[0]
    assert intent.classify(REPORT_OK, meta, 0.6).hints == []
    assert intent.worth_asking(REPORT_OK, meta)
    assert not intent.worth_asking(REPORT_OK, Meta(agent_authored=True, automated=True))
    assert not intent.worth_asking(REPORT_OK, Meta())
    assert not intent.worth_asking(BARE, meta)
    assert not intent.worth_asking("ок", meta)
    assert not intent.worth_asking(REPORT_OK, Meta(agent_authored=True, tag="decision"))


def test_tg_bot_asks_laya(fake_engines, laya_server, monkeypatch):
    laya_server.answers = {"asks_reader": 0.95}
    report = run("tg-bot", input_doc([("body", REPORT_OK, "plain")], agent_authored=True))
    (request,) = laya_server.requests
    assert sorted(request["questions"]) == ["asks_reader", "slop"]
    assert report["intent"]["laya_p"] == 0.95
    assert "p=0.95" in report["intent"]["hints"][0]
    assert report["engines"]["laya"]["answers"] == {"asks_reader": 0.95, "slop": 0.1}
    # Every profile asks Laya its slop question, in one request per run (Main relaying the owner).
    for profile in ("prose", "commit", "pr"):
        laya_server.requests.clear()
        report = run(profile, input_doc([("body", REPORT_OK, "plain")], agent_authored=True))
        (request,) = laya_server.requests
        assert list(request["questions"]) == ["slop"], profile
        assert "intent" not in report
    # On a CI runner with no socket answering, Laya is not run; nothing is started.
    laya_server.requests.clear()
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("TEXT_LINT_LAYA_SOCKET", str(laya_server.path) + ".gone")
    report = run("pr", input_doc([("body", REPORT_OK, "plain")]), laya_mode="ci")
    assert report["engines"]["laya"] == {"status": "not run", "reason": "ci runner, no local model"}
    assert laya_server.requests == []


def test_intent_report_shape(fake_engines):
    report = run("tg-bot", input_doc([("body", BARE, "plain")], agent_authored=True))
    assert report["intent"] == {
        "kinds": ["decision"], "action": "block", "reason": "malformed-decision", "follow_ups": [],
        "valid": None, "hints": [], "laya_p": None,
    }
    assert report["verdict"] == "block"
    report = run("tg-bot", input_doc([("body", COMPLIANT, "html")], tag="decision", agent_authored=True,
                                     send_format="html"))
    assert report["intent"]["valid"] == "decision" and report["verdict"] != "block"


@pytest.mark.parametrize("body, action", [
    ("ок, принял, начинаю", "pass"),
    ("Мержим? Или ждём ревью?", "block"),
    ("Почему падало? Флейк в ctl-тестах, починил ретраем, #690, тесты зелёные.", "pass"),
    ("Ты спросил «зачем нужен X?» — отвечаю: для кэша.", "pass"),
    ("I am not done yet; still investigating.", "pass"),
])
def test_detection(body, action):
    assert intent.classify(body, Meta(agent_authored=True), None).action == action


@pytest.mark.parametrize("body, signal, expected", [
    ("Мержим? Или ждём ревью?", "asks", ["Мержим? Или ждём ревью?"]),
    ("Два вопроса: 1) мержить #694 сейчас? 2) удалять ветку?", "asks", 2),
    ("Жду твоего решения по #566.", "asks", 1),
    ("Выбери вариант, пожалуйста.", "asks", 1),
    ("Let me know which one you prefer.", "asks", 1),
    ("Мержу завтра, если нет возражений.", "asks", 1),
    ("Ты спросил «зачем нужен X?» — отвечаю: для кэша.", "asks", []),
    ("<pre>if (ok?) run()</pre> Всё собрано.", "asks", []),
    ("Смотри `a ? b : c` и https://x.com/a?b=1 — всё там.", "asks", []),
    ("FAILED tg#? sessions=0", "asks", []),
    ("Почему падало? Флейк в ctl-тестах, починил ретраем.", "asks", []),
    ("Мержить или ждать? Я бы мержил.", "asks", 1),
    ("CI красный из-за флейка.", "problems", []),
    ("Блокер: нет доступа к staging.", "problems", 1),
    ("I am blocked on the deploy key.", "problems", 1),
    ("Смержил #690, задеплоил.", "reports", 1),
    ("Надо смержить #690.", "reports", []),
    ("I did not touch docs; implemented #706, tests pass.", "reports", 1),
    ("Доки не трогал, смержил #706.", "reports", 1),
    ("Ещё не смержил #706.", "reports", []),
    ("Not only merged #706; tests passed.", "reports", 1),
    ("Не только смержил #706, но и прогнал тесты.", "reports", 1),
    ("Not merged yet.", "reports", []),
])
def test_source_signal_assertions(body, signal, expected):
    # Exact assertions from tg-cli tests/intent-gate.test.ts:63-90,245-249,282-287.
    # The JSONL intent corpus also classifies these bodies, but that alone cannot prove
    # signal arrays/counts: several different detector results give the same action.
    got = getattr(intent.detect_signals(body), signal)
    assert (len(got) if isinstance(expected, int) else got) == expected
