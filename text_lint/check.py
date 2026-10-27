"""One check run: units → engines → suppressions → report.

All units of a run go to Vale in one call (one file per unit) and to aiw-ru in one call.
Their texts are joined with blank lines into one document that decides paragraph languages,
word counts and aiw-ru's input; every finding is placed in that document, then mapped back
to its unit (field, gate, line and column in the source) through the offset where that unit
starts.
"""

import bisect
import os
import re
from dataclasses import replace

from .engines import EngineMissing, aiwru, laya, native, vale
from .native import intent, message_unit
from .profiles import ConfigError
from .report import Finding, build_report, capped
from .text import (
    LineIndex,
    has_cyrillic,
    is_russian,
    mask,
    paragraphs,
    protected_spans,
    url_spans,
)

SEVERITY_WEIGHT = {"error": 3, "warning": 2, "suggestion": 1}
# Ten weighted points per hundred words (three errors and a warning) is a score of 100.
POINTS_PER_100_WORDS_AT_MAX = 10
_WORD = re.compile(r"\w+")
# Between two units, so no paragraph spans two fields.
_SEPARATOR = "\n\n"


def check_units(units, meta, profile, *, enforce, lang="auto", ignore=(), fail_on=None,
                laya_mode="required", suppress=None, dict_path=None, from_input_json=False):
    """The report@1 dict for ``units``.

    ``lang`` routes the text as `--lang` does: `en` skips aiw-ru, `ru` drops ai-tells.
    ``fail_on`` overrides the profile's. ``laya_mode="ci"`` (only with CI=true) runs Laya when
    a socket answers and reports it as not run otherwise, without starting a server.
    ``suppress(findings) -> (kept, suppressed records, extra findings)`` runs before the
    verdict. ``meta`` carries the message meta data (tag, sender, attachments) for the native
    message rules. ``dict_path`` is `--dict`; ``from_input_json`` says the units are input@1
    fields rather than one file or stdin.
    """
    if fail_on is not None:
        profile = replace(profile, fail_on=fail_on)
    native_spec = profile.engines["native"]
    # Loaded first: a broken pack or dictionary ends the run before any engine starts.
    native_rules, native_status = (native.rules_for(profile, dict_path=dict_path) if native_spec.enabled
                                   else ([], _off(profile)))
    # The run reports its intent verdict when the profile runs the intent classifier.
    reports_intent = any(rule.kind == "intent" for rule in native_rules)
    doc = _Document(units)
    if native_rules and not any(u.raw.strip() for u in doc.units):
        # Nothing to read: no rule runs, except the format rules of a tagged message, for which an
        # empty body is malformed (tg-cli refuses an empty `--tag decision` send).
        native_rules = [rule for rule in native_rules if rule.kind == "format" and meta.tag is not None]
        if native_rules:
            native_status["rules"] = len(native_rules)
        else:
            # Reported like Vale's empty input.
            native_status = {key: value for key, value in native_status.items() if key != "rules"}
            native_status.update(status="skipped", reason="empty input")
    text = doc.text
    # Code and URLs are blanked only to decide languages and count words. The engines get
    # the real Markdown (blanking an inline code span at the start of a list item would turn
    # the item into an indented code block that Vale skips), and any finding that touches
    # code or a URL is dropped below.
    protected = doc.protected
    masked = mask(text, protected)
    index = LineIndex(text)
    # Blocks come from the real text: a line that is only a URL or inline code must not
    # split its paragraph. Blocks with nothing left after masking (pure code) are skipped.
    paras = [p for p in paragraphs(text) if masked[p[0]:p[1]].strip()]
    if lang == "auto":
        ru_paras = [p for p in paras if is_russian(masked[p[0]:p[1]])]
        # aiw-ru also reads English-majority paragraphs with Russian in them.
        aiw_paras = [p for p in paras if has_cyrillic(masked[p[0]:p[1]])]
    else:
        ru_paras = aiw_paras = paras if lang == "ru" else []
    en_paras = [p for p in paras if p not in ru_paras]

    # Laya first: native kinds read its answers.
    message = message_unit(doc.units, from_input_json=from_input_json)
    laya_status, laya_findings = _laya(doc, profile, laya_mode, ignore, meta, message)
    # Native findings are located in their unit already: they read the normalized text, which
    # has its code masked, so nothing of theirs is dropped for touching code below. For the
    # score, each is also placed in the joined document (its language is its paragraph's).
    ctx = native.context(doc.units, meta, from_input_json=from_input_json, laya=laya_status.get("answers"))
    native_findings, native_placed = [], []
    for i, unit in enumerate(doc.units):
        for f in native.run_unit(unit, native_rules, ctx):
            if not _ignored(f.rule_id, ignore):
                native_findings.append(_cap(f, native_spec.cap))
                start = doc.offset_of(i, f.span[0])
                native_placed.append(replace(native_findings[-1], span=(start, start)))
    engines = {"native": native_status}
    findings = []
    spec = profile.engines["vale"]
    if not spec.enabled:
        engines["vale"] = _off(profile)
    elif not paras:
        engines["vale"] = {"status": "skipped", "reason": "empty input"}
    else:
        try:
            ext = spec.options["ext"]
            # Plain and HTML (already stripped) text is read by Vale as plain text: markup the
            # reader sees literally must not switch rules off.
            texts = [(u.text, ext if u.format == "markdown" else f"{ext}.txt") for u in doc.units]
            vale_findings = doc.place(vale.run(texts), index)
            engines["vale"] = {"status": "ok"}
        except EngineMissing as exc:
            if not spec.optional:
                raise
            vale_findings = []
            engines["vale"] = {"status": "skipped", "reason": f"not installed ({exc.problem})"}
        disabled = spec.options.get("disable", [])
        findings += [_cap(f, spec.cap) for f in vale_findings
                     if _keep_vale(f, ru_paras, index, lang) and not _ignored(f.rule_id, disabled)]

    aiwru_score = 0
    spec = profile.engines["aiw-ru"]
    if not spec.enabled:
        engines["aiw-ru"] = _off(profile)
    elif not aiw_paras:
        reason = "--lang en" if lang == "en" else "no Russian text"
        engines["aiw-ru"] = {"status": "skipped", "reason": reason}
    else:
        others = [p for p in paras if p not in aiw_paras]
        try:
            aiw_findings, aiwru_score, label = aiwru.run(mask(text, others + protected),
                                                         spec.options["context"], index)
            engines["aiw-ru"] = {"status": "ok", "score": aiwru_score, "label": label}
            findings += [_cap(f, spec.cap) for f in aiw_findings]
        except EngineMissing as exc:
            if not spec.optional:
                raise
            engines["aiw-ru"] = {"status": "skipped", "reason": f"not installed ({exc.problem})"}

    kept = [f for f in findings
            if not _overlaps(protected, f.span) and not _ignored(f.rule_id, ignore)]
    aiwru_score = _surviving_share(aiwru_score, findings, kept)
    findings = kept
    score = _score(findings + native_placed, masked, en_paras, ru_paras, aiwru_score)

    engines["laya"] = laya_status
    findings = [doc.locate(f) for f in sorted(findings, key=lambda f: (f.span[0], f.span[1], f.rule_id))]
    findings = laya_findings + native_findings + findings
    suppressed = []
    if suppress is not None:
        findings, suppressed, extra = suppress(findings)
        findings = findings + extra
    return build_report(profile=profile, enforce=enforce, findings=findings, suppressed=suppressed,
                        engines=engines, score=score, intent=ctx.intent.as_report() if reports_intent else None)


class _Document:
    """The units' normalized texts joined into one document (languages, word counts, aiw-ru)."""

    def __init__(self, units):
        self.units = list(units)
        self.starts = []
        parts = []
        pos = 0
        for unit in self.units:
            self.starts.append(pos)
            parts.append(unit.text)
            pos += len(unit.text) + len(_SEPARATOR)
        self.text = _SEPARATOR.join(parts)
        self.protected = sorted(
            (start + s, start + e)
            for unit, start in zip(self.units, self.starts)
            for s, e in _protected(unit))

    def place(self, per_unit, index):
        """Findings reported per unit (``per_unit[i]`` within unit ``i``'s text) as findings
        in the joined document, whose LineIndex is ``index``."""
        placed = []
        for base, findings in zip(self.starts, per_unit):
            for f in findings:
                span = (f.span[0] + base, f.span[1] + base)
                line, col = index.line_col(span[0])
                placed.append(replace(f, span=span, line=line, col=col))
        return placed

    def offset_of(self, i, raw_offset):
        """The joined-document offset of ``raw_offset`` in unit ``i``'s field as sent: its
        first normalized character at or after that offset."""
        unit = self.units[i]
        offset = raw_offset if unit.norm_to_raw is None else bisect.bisect_left(unit.norm_to_raw, raw_offset)
        return self.starts[i] + min(offset, len(unit.text))

    def locate(self, finding):
        """``finding`` (offsets into the joined document) as a finding of its unit: line, col
        and span point into the field as sent (through any HTML stripping)."""
        i = bisect.bisect_right(self.starts, finding.span[0]) - 1
        unit, base = self.units[i], self.starts[i]
        start = finding.span[0] - base
        end = max(start, min(finding.span[1] - base, len(unit.text)))
        raw_start, raw_end = unit.raw_span(start, end)
        line, col = unit.position(start, space="text")
        return replace(finding, line=line, col=col, span=(raw_start, raw_end), field=unit.field,
                       path=unit.path, key=unit.key, gate=unit.gate)

    def laya_text(self, max_chars):
        """What Laya reads: the gating units joined, cut to ``max_chars`` (Laya reads a few
        hundred tokens; the head of a long message is what it sees anyway)."""
        return _SEPARATOR.join(u.text for u in self.units if u.gate and u.text.strip())[:max_chars]


def _protected(unit):
    """Spans of ``unit.text`` no finding may touch: code and URLs in Markdown, URLs in HTML
    (its code is already blanked by the stripping; backticks there are literal text); plain
    text is checked as written."""
    if unit.format == "markdown":
        return protected_spans(unit.text)
    if unit.format == "html":
        return url_spans(unit.text)
    return []


def _off(profile):
    return {"status": "skipped", "reason": f"off in profile {profile.name}"}


def _cap(finding, cap):
    severity = capped(finding.severity, cap)
    return finding if severity == finding.severity else replace(finding, severity=severity)


# Reported when `--laya-mode ci` finds no socket answering on a CI runner.
CI_NOT_RUN = {"status": "not run", "reason": "ci runner, no local model"}


def _laya(doc, profile, laya_mode, ignore, meta, message):
    """(engines["laya"], findings): one request carrying every question the run asks.

    Laya is required: when it cannot answer the run ends (LayaUnavailable, exit 2), except on
    a CI runner (`--laya-mode ci`: not run) and for an automated message, which a program
    cannot fix by waiting (the owner's delegated ruling B: reported as unreachable, and the
    other engines decide the verdict)."""
    spec = profile.engines["laya"]
    if not spec.enabled:
        return {"status": "skipped", "reason": f"profile {profile.name} asks Laya nothing"}, []
    on_ci = laya_mode == "ci"
    if on_ci and os.environ.get("CI") != "true":
        raise ConfigError("--laya-mode ci is for CI runners and needs CI=true in the environment")
    text = doc.laya_text(spec.options["max_chars"])
    if not text.strip():
        return {"status": "skipped", "reason": "empty input"}, []
    # asks_reader serves the intent classifier only, and only where its answer can count.
    questions = [q for q in spec.options["questions"]
                 if q != "asks_reader" or (message is not None and intent.worth_asking(message.raw, meta))]
    try:
        result = laya.ask(text, questions, timeout_s=laya.request_timeout_s(spec.options["timeout_ms"]),
                          start_wait_s=spec.options["start_wait_ms"] / 1000, start=not on_ci)
        missing = [q for q in questions if result["answers"].get(q) is None]
        if missing:
            raise laya.LayaUnavailable(f"missing required Laya answer(s): {', '.join(missing)}")
    except laya.LayaUnavailable as exc:
        if on_ci and isinstance(exc, laya.LayaNotListening):
            return dict(CI_NOT_RUN), []
        if meta.automated:
            return {"status": "unreachable", "reason": exc.problem}, []
        raise
    findings = []
    slop = result["answers"].get("slop")
    if slop is not None and slop >= laya.ADVISORY_THRESHOLD and not _ignored("laya.SlopProbability", ignore):
        findings.append(Finding(
            rule_id="laya.SlopProbability", engine="laya", severity="suggestion",
            line=1, col=1, span=(0, 0), match="",
            message=f"Laya rates the whole text as slop with p={slop:.2f} (advisory only)",
        ))
    return {"status": "ok", **result}, findings


def _in(paras, offset):
    return any(s <= offset < e for s, e in paras)


def _overlaps(spans, span):
    start, end = span
    return any(s < max(end, start + 1) and start < e for s, e in spans)


def _keep_vale(finding, ru_paras, index, lang):
    if finding.rule_id.startswith("ai-tells"):
        # The vendored styles are English-only. In a Russian paragraph they misread Russian
        # punctuation and sentence shapes, so only a match in English (an English sentence
        # inside a Russian paragraph) is kept; --lang ru drops them all.
        if _in(ru_paras, finding.span[0]) and (lang == "ru" or not _is_english(finding.match)):
            return False
        # The em dash is ordinary Russian punctuation on any Cyrillic line.
        if finding.rule_id == "ai-tells.EmDashUsage" and has_cyrillic(index.line_text(finding.line)):
            return False
    return True


def _is_english(match):
    return re.search(r"[A-Za-z]", match) is not None and not has_cyrillic(match)


def _ignored(rule_id, ignore):
    return any(rule_id == rule or rule_id.startswith(rule + ".") for rule in ignore)


def _density_score(findings, words):
    if not words:
        return 0
    points = sum(SEVERITY_WEIGHT.get(f.severity, 1) for f in findings)
    return min(100, round(points * 100 * 100 / (words * POINTS_PER_100_WORDS_AT_MAX)))


def _surviving_share(aiwru_score, findings, kept):
    """aiw-ru's score cut to the weight of its findings still reported: one dropped for code,
    a URL or --ignore must not keep scoring. A score with no findings behind it stays."""
    def weight(fs):
        return sum(SEVERITY_WEIGHT.get(f.severity, 1) for f in fs if f.engine == "aiw-ru")
    total = weight(findings)
    return round(aiwru_score * weight(kept) / total) if total else aiwru_score


def _words(text, paras):
    return sum(len(_WORD.findall(text[s:e])) for s, e in paras)


def _score(findings, masked, en_paras, ru_paras, aiwru_score):
    """0-100. English: weighted findings per 100 words. Russian: aiw-ru's own score, or the
    density of our own findings (TextLint and native rules) when that is higher. Mixed:
    word-weighted average."""
    en_words, ru_words = _words(masked, en_paras), _words(masked, ru_paras)
    en = _density_score([f for f in findings if not _in(ru_paras, f.span[0])], en_words)
    ours_in_ru = [f for f in findings if (f.rule_id.startswith("TextLint.") or f.engine == "native")
                  and _in(ru_paras, f.span[0])]
    ru = max(aiwru_score, _density_score(ours_in_ru, ru_words))
    total = en_words + ru_words
    return round((en * en_words + ru * ru_words) / total) if total else 0
