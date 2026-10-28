"""The intent classifier and the `intent` kind (Intent.*): what a message is (a decision ask,
a problem, a report) whether or not it carries a tag, and whether each part follows its
format. A port of tg-cli's intent gate (features/cli/intent-gate.ts: `detectSignals`:210,
`classifyIntent`:342, `layaWorthAsking`:333), keeping its bilingual detection and messages.

Verdicts: `pass` (nothing asked, or every part in its format), `follow-up` (sendable, but it
also asks something outside a decision request, carries several decisions or hides a blocker:
one reply is owed per item) and `block` (a malformed decision, problem or report, or a mix
of all three). Laya's `asks_reader` probability only ever adds a hint.

It runs on the message unit of an agent-authored or automated message (tg:1708-1715); a
person's own message is not classified. `--automated` skips the problem and report formats,
is never asked about by Laya and never settles a follow-up (`valid`), but a prose question in
it still blocks.
"""

import re
from dataclasses import dataclass, field, replace
from typing import Literal

from . import finding
from .formats import (
    NEED_RE,
    OPTIONS_HEADING_RE,
    TAG_RE,
    decision_labels,
    decode_entities,
    split_sections,
    validate_decision,
    validate_problem,
    validate_report,
)
from .jsre import LN, WE, B, L, W, js_len

# Laya at or above this probability adds a hint when the heuristic found no ask (tg-cli#706).
LAYA_ASK_THRESHOLD = 0.9


@dataclass
class FollowUp:
    kind: Literal["decision", "problem"]
    quote: str


@dataclass
class Intent:
    kinds: list[str]
    action: Literal["pass", "follow-up", "block"]
    reason: str | None = None
    follow_ups: list[FollowUp] = field(default_factory=list)
    valid: Literal["decision", "problem"] | None = None
    hints: list[str] = field(default_factory=list)
    # A block's checklist: what is missing, and the asks that caused it.
    detail: list[str] = field(default_factory=list)
    # Laya's asks_reader probability, when it was asked.
    laya_p: float | None = None

    def as_report(self):
        """report@1's `intent` object."""
        return {"kinds": list(self.kinds), "action": self.action, "reason": self.reason,
                "follow_ups": [{"kind": f.kind, "quote": f.quote} for f in self.follow_ups],
                "valid": self.valid, "hints": list(self.hints), "laya_p": self.laya_p}


# --- the sender's prose ---

# Elements whose contents are not the sender's own prose: code, quotes, formulas.
_QUOTED_ELEMENT_RE = re.compile(rf"<(pre|code|blockquote|aside|tg-math-block|tg-math){B}[^>]*>[\s\S]*?</\1\s*>", re.IGNORECASE)
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?(?:```|\Z)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(rf"{B}[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.IGNORECASE)
_BLOCK_BREAK_RE = re.compile(
    rf"</?(?:h[1-6]|p|tr|td|th|li|ul|ol|table|thead|tbody|tfoot|caption|details|summary|footer|figure|figcaption|div)"
    rf"{B}[^>]*>|<(?:br|hr){B}[^>]*>",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=\S)")
_HAS_WORD_RE = re.compile(LN)


def sender_prose(body: str) -> str:
    """The sender's visible prose: quoted and code text and URLs removed, block tags turned
    into line breaks, inline tags stripped, entities decoded."""
    text = _QUOTED_ELEMENT_RE.sub(" ", body)
    text = _FENCED_CODE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _BLOCK_BREAK_RE.sub("\n", text)
    return decode_entities(TAG_RE.sub("", text))


def _prose_lines(body):
    lines = []
    for line in re.split(r"\r?\n", sender_prose(body)):
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(line)]
        sentences = [s for s in sentences if _HAS_WORD_RE.search(s)]
        if sentences:
            lines.append(sentences)
    return lines


# --- signals ---

# A question mark closing a clause of words (not `tg#?`, not a lone "?").
_QUESTION_RE = re.compile(r"(?:[^\W_]|[)»\"'”\]])\s*\?+(?=[\s)»\"'”\].,!…:;—–-]|\Z)")
# Decision imperatives / explicit requests for the reader's call, without a "?".
_DECISION_IMPERATIVE_RE = re.compile("|".join([
    (rf"{W}(?:реши|решай|решите|выбери|выбирай|выберите|подтверди|подтвердите|одобри|одобрите|согласуй|согласуйте"
    rf"|определись|определитесь){WE}"),
    rf"{W}да(?:й|йте) (?:знать|добро|ответ|отмашку|решение|ок){WE}",
    rf"{W}жду (?:твоего |вашего )?(?:решения|ответа|подтверждения|одобрения|отмашки|ок|добро){WE}",
    (rf"{W}(?:нужно|нужен|нужна|нужны|требуется) (?:твоё|твое|ваше|твой|ваш|твоя|ваша) "
    rf"(?:решение|одобрение|подтверждение|ок|ответ|выбор|мнение){WE}"),
    rf"{W}на (?:твоё|твое|ваше) усмотрение{WE}",
    rf"{W}как (?:поступим|поступаем|быть){WE}",
    rf"{W}если (?:нет|не будет) возражений{WE}",
    (rf"{B}(?:let me know|please (?:decide|confirm|choose|approve|advise)|your call|up to you"
    rf"|need your (?:decision|approval|ok|call|input)|waiting (?:for|on) your (?:decision|approval|answer|ok|go)"
    rf"|if (?:there are )?no objections|unless you object){B}"),
]), re.IGNORECASE)
# A question the sender answers in the very next sentence of the same line.
_RHETORICAL_RE = re.compile(
    rf"^(?:{W}(?:почему|зачем|из-за чего|в ч[её]м (?:причина|дело|проблема)"
    rf"|что (?:случилось|произошло|сломалось|было|изменилось|сделано|в итоге)){WE}"
    rf"|{B}(?:why|what (?:happened|broke|changed|was done)|root cause){B})",
    re.IGNORECASE,
)
# A sentence that continues the previous ask ("Мержим? Или ждём?").
_CONTINUATION_RE = re.compile(rf"^{W}(?:или|либо){WE}|^or{B}", re.IGNORECASE)
# A blocker the sender cannot clear alone. Explicit markers only.
_PROBLEM_RE = re.compile("|".join([
    rf"{W}(?:блокер{L}*|блокирует|заблокирован{L}*|застрял{L}*){WE}",
    (rf"{W}не (?:могу|можем|получается) (?:продолжить|двигаться|закончить|выполнить|запустить|получить|задеплоить"
    rf"|смержить){WE}"),
    rf"{W}нет доступа{WE}",
    rf"{W}без (?:доступа|токена|прав|ключа|пароля) не{WE}",
    rf"{W}нуж(?:ен|на|но|ны) (?:доступ|токен|ключ|пароль|права|твоя помощь|ваша помощь|помощь){WE}",
    rf"{W}требуется (?:доступ|токен|вмешательство|помощь){WE}",
    (rf"{B}(?:blocker|blocked (?:on|by)|i am blocked|i'm blocked|we are blocked|stuck on|cannot proceed"
    rf"|can't proceed|cannot continue|can't continue|need (?:your )?(?:access|a token|credentials|help)){B}"),
]), re.IGNORECASE)
# Finished work: a completed-action verb or a results label.
_REPORT_RE = re.compile("|".join([
    rf"{W}(?:готово|сделано|выполнено|итоги?:|отч[её]т:)",
    (rf"{W}(?:сделал|выполнил|смержил|смерджил|замержил|задеплоил|выкатил|починил|исправил|закрыл|завершил"
    rf"|реализовал|добавил|запушил|опубликовал|выпустил)(?:а|и)?{WE}"),
    (rf"{W}(?:смержен|смерджен|замержен|задеплоен|выкачен|починен|исправлен|закрыт|завершен|завершён|реализован"
    rf"|добавлен|выпущен)(?:а|о|ы)?{WE}"),
    rf"{B}(?:done|merged|deployed|shipped|fixed|implemented|completed|finished|released|landed){B}",
]), re.IGNORECASE)
# An explicitly absent blocker ("Блокера нет", "no blockers") is not a problem.
_NO_BLOCKER_RE = re.compile(
    rf"{W}(?:блокер{L}*\s+нет|нет\s+блокер{L}*|без\s+блокер{L}*|ничего\s+не\s+блокирует|не\s+заблокирован{L}*"
    rf"|разблокирован{L}*){WE}|{B}(?:no\s+blockers?|not\s+blocked|nothing\s+(?:is\s+)?blocking|unblocked){B}",
    re.IGNORECASE,
)
# Unfinished work is not a report; checked per clause. "Not only" adds rather than negates.
_NEGATED_RE = re.compile(
    rf"{W}(?:не|пока не|ещё не|еще не){WE}(?!\s+только{WE})|{B}(?:not(?!\s+only{B})|never|yet){B}|n't{B}", re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(rf"[;,—–]|{W}(?:но|а){WE}|{B}but{B}", re.IGNORECASE)
_QUOTED_SPEECH_RE = re.compile(r"«[^«»]*»|“[^“”]*”|\"[^\"\n]*\"")


def _clip(text):
    t = re.sub(r"\s+", " ", text).strip()
    if js_len(t) <= 160:
        return t
    return t.encode("utf-16-le")[:157 * 2].decode("utf-16-le", errors="ignore") + "…"


@dataclass
class Signals:
    asks: list[str]
    problems: list[str]
    reports: list[str]


def detect_signals(body):
    """The asks, blocker sentences and finished-work sentences in the sender's prose."""
    sig = Signals([], [], [])
    for line in _prose_lines(body or ""):
        previous_was_ask = False
        for i, sentence in enumerate(line):
            # Quoted speech is someone else's words: a question inside it is reported, not asked.
            own = _QUOTED_SPEECH_RE.sub(" ", sentence)
            imperative = bool(_DECISION_IMPERATIVE_RE.search(own))
            question = bool(_QUESTION_RE.search(own))
            following = line[i + 1] if i + 1 < len(line) else None
            self_answered = (question and not imperative and _RHETORICAL_RE.search(own)
                             and following is not None and not _QUESTION_RE.search(following))
            if (imperative or question) and not self_answered:
                if previous_was_ask and _CONTINUATION_RE.search(sentence):
                    sig.asks[-1] = _clip(f"{sig.asks[-1]} {sentence}")
                else:
                    sig.asks.append(_clip(sentence))
                previous_was_ask = True
                continue
            previous_was_ask = False
            if _PROBLEM_RE.search(own) and not _NO_BLOCKER_RE.search(own):
                sig.problems.append(_clip(sentence))
            elif any(_REPORT_RE.search(c) and not _NEGATED_RE.search(c) for c in _CLAUSE_SPLIT_RE.split(own)):
                sig.reports.append(_clip(sentence))
    return sig


# --- classification ---

_PRIMARY_TAGS = {"decision": "decision", "problem": "problem", "report": "report"}
# The sections of a decision request a question legitimately sits in, besides Options.
_DECISION_PART_NAME_RE = re.compile(
    r"^(?:[0-9]+[.)]?\s*)?(?:context|background|problem|question|issue|decision|recommendations?"
    r"|контекст|проблема|вопрос|решение|рекомендаци[яи])(?:\s*[0-9]+)?(?:\s*[:.—–-].*)?\Z",
    re.IGNORECASE,
)


def worth_asking(text, meta):
    """Whether Laya's `asks_reader` answer can change anything: an agent's message, not
    automated, not tagged `decision`, of 40 visible characters or more, in which the heuristic
    found no ask (layaWorthAsking)."""
    if not meta.agent_authored or meta.automated or meta.tag == "decision":
        return False
    if js_len(decode_entities(TAG_RE.sub("", text)).strip()) < 40:
        return False
    return not detect_signals(text).asks


def classify(text, meta, laya_p):
    """The intent verdict for message ``text`` (classifyIntent). ``meta.tag`` and
    ``meta.automated`` are read; ``laya_p`` is Laya's asks_reader probability or None."""
    body = text or ""
    tag = meta.tag
    sig = detect_signals(body)
    kinds = []
    if sig.asks or tag == "decision":
        kinds.append("decision")
    if sig.problems or tag == "problem":
        kinds.append("problem")
    if sig.reports or tag == "report":
        kinds.append("report")

    hints = []
    if not sig.asks and laya_p is not None and laya_p >= LAYA_ASK_THRESHOLD:
        hints.append(f"laya reads this message as asking the reader something (p={laya_p:.2f}), but no question "
                     "sentence was found. If it does ask, send that question as its own --tag decision message.")

    def verdict(action, **kw):
        return Intent(kinds=kinds, action=action, hints=hints, laya_p=laya_p, **kw)

    # A decision request already in its format: the rest of the body is its context. A
    # question outside every section the format defines is a second ask riding along.
    missing = validate_decision(body)
    if not missing:
        if "decision" not in kinds:
            kinds.insert(0, "decision")
        labels = decision_labels(body)
        extra_asks = [ask for heading, section in split_sections(body)
                      if heading == "" or (not OPTIONS_HEADING_RE.search(heading)
                                           and not _DECISION_PART_NAME_RE.search(heading))
                      for ask in detect_signals(section).asks]
        if len(labels) > 1 or extra_asks:
            reason = "; ".join(part for part in (
                f"this message carries {len(labels)} separate decisions — each one needs its own message"
                if len(labels) > 1 else "",
                "it also asks a question outside its decision request" if extra_asks else "") if part)
            follow_ups = ([FollowUp("decision", label) for label in labels] if len(labels) > 1 else [])
            follow_ups += [FollowUp("decision", ask) for ask in extra_asks]
            return verdict("follow-up", reason=reason, follow_ups=follow_ups)
        if tag != "decision":
            hints.append("This is a decision request: send it with --tag decision.")
        # A program's message never settles an agent's owed follow-up.
        return verdict("pass", valid=None if meta.automated else "decision")

    if len(kinds) == 3:
        return verdict("block", reason="mixed", detail=[
            f"decision: «{sig.asks[0] if sig.asks else '(tagged)'}»",
            f"problem: «{sig.problems[0] if sig.problems else '(tagged)'}»",
            f"report: «{sig.reports[0] if sig.reports else '(tagged)'}»",
        ])

    # Untagged: a report is the sendable carrier; a blocker whose only questions are its own
    # requests is a problem; any other question makes it a decision.
    requests_only = all(NEED_RE.search(q) for q in sig.asks)
    primary = _PRIMARY_TAGS.get(tag) if tag is not None else None
    if primary is None:
        if tag == "answer":
            primary = "answer"
        elif "report" in kinds:
            primary = "report"
        elif "problem" in kinds and requests_only:
            primary = "problem"
        elif "decision" in kinds:
            primary = "decision"
    if primary is None:
        return verdict("pass")

    asks = [FollowUp("decision", q) for q in sig.asks]
    if primary == "answer" and not asks:
        return verdict("pass")

    # An answer that is nothing but a question back is a bare decision ask.
    answer_prose = " ".join(s for line in _prose_lines(body) for s in line
                            if not any(_clip(s) in q or q in _clip(s) for q in sig.asks))
    bare_question = primary == "answer" and len(re.findall(LN, answer_prose)) < 20
    if primary == "decision" or bare_question:
        detail = [f"ask: «{q}»" for q in sig.asks]
        if len(asks) > 1:
            detail.append(f"It asks {len(asks)} separate questions — each one is its own decision message.")
        return verdict("block", reason="malformed-decision", detail=detail + missing)

    if meta.automated or primary == "answer":
        check = []
    else:
        check = validate_problem(body) if primary == "problem" else validate_report(body)
    # A problem's own request ("дашь доступ к staging?") is its ask, not a separate decision.
    choice_asks = [a for a in asks if not NEED_RE.search(a.quote)] if primary == "problem" else asks
    if check:
        return verdict("block", reason="malformed-problem" if primary == "problem" else "malformed-report",
                       detail=check + [f"and the question «{a.quote}» goes out as its own --tag decision message"
                                       for a in choice_asks])
    # A blocker hidden in a report is its own problem message; inside a problem it is the point.
    follow_ups = choice_asks + ([FollowUp("problem", q) for q in sig.problems] if primary == "report" else [])
    if not follow_ups:
        return verdict("pass", valid="problem" if primary == "problem" and not meta.automated else None)
    if not choice_asks:
        reason = f"this {primary} also reports a blocker — the blocker is its own --tag problem message"
    elif len(choice_asks) == 1:
        reason = "this message asks a question that is not a decision request"
    else:
        reason = f"this message asks {len(choice_asks)} questions that are not decision requests"
    return verdict("follow-up", reason=reason, follow_ups=follow_ups)


NOT_CLASSIFIED = Intent(kinds=[], action="pass")


def of_message(unit, meta, laya_p):
    """The run's intent: classified for the message unit of an agent-authored or automated
    message; a person's own message, or a run without a message unit, passes unclassified."""
    if unit is None or not (meta.agent_authored or meta.automated):
        return NOT_CLASSIFIED
    return classify(unit.semantic, meta, laya_p)


def run(rule, unit, ctx):
    """The `intent` kind: one finding on the message unit when the run's intent blocks for
    ``params.reason``. A tagged message's own format rule (Format.<Tag>) reports a malformed
    message of its tag, so it is not reported twice (tg:1720)."""
    reason = rule.params["reason"]
    verdict = ctx.intent
    if unit is not ctx.message or verdict.action != "block" or verdict.reason != reason:
        return []
    if ctx.meta.tag in _PRIMARY_TAGS and reason == f"malformed-{ctx.meta.tag}":
        return []
    hit = finding(rule, unit, 0, 0, space="semantic", match="")
    return [replace(hit, message=hit.message + " Fix:" + "".join(f" • {item}" for item in verdict.detail))]
