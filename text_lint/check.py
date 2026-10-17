"""One check run: route paragraphs to the engines, merge their findings, score the text."""

import re

from . import __version__, laya
from .engines import Finding, run_aiwru, run_vale, vale_version
from .text import LineIndex, has_cyrillic, is_russian, mask, paragraphs, protected_spans

SEVERITY_WEIGHT = {"error": 3, "warning": 2, "suggestion": 1}
# Ten weighted points per hundred words (three errors and a warning) is a score of 100.
POINTS_PER_100_WORDS_AT_MAX = 10
_WORD = re.compile(r"\w+")


def check(text, *, lang="auto", context=None, ignore=(), use_laya=False):
    """The report dict: {"version", "score", "findings", "engines"}."""
    # Code and URLs are blanked only to decide languages and count words. The engines get
    # the real Markdown (blanking an inline code span at the start of a list item would turn
    # the item into an indented code block that Vale skips), and any finding that touches
    # code or a URL is dropped below.
    protected = protected_spans(text)
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

    engines = {}
    findings = []
    if paras:
        vale_findings = run_vale(text, context, index)
        engines["vale"] = {"status": "ok", "version": vale_version()}
        findings += [f for f in vale_findings if _keep_vale(f, ru_paras, index, lang)]
    else:
        engines["vale"] = {"status": "skipped", "reason": "empty input"}

    aiwru_score = 0
    if aiw_paras:
        others = [p for p in paras if p not in aiw_paras]
        aiw_findings, aiwru_score, label = run_aiwru(mask(text, others + protected), context, index)
        engines["aiw-ru"] = {"status": "ok", "score": aiwru_score, "label": label}
        findings += aiw_findings
    else:
        reason = "--lang en" if lang == "en" else "no Russian text"
        engines["aiw-ru"] = {"status": "skipped", "reason": reason}

    kept = [f for f in findings
            if not _overlaps(protected, f.span) and not _ignored(f.rule_id, ignore)]
    aiwru_score = _surviving_share(aiwru_score, findings, kept)
    findings = kept
    score = _score(findings, masked, en_paras, ru_paras, aiwru_score)

    if use_laya:
        advice = laya.advise(text)
        engines["laya"] = advice
        if advice["status"] == "ok" and advice["noul"] >= laya.ADVISORY_THRESHOLD \
                and not _ignored("laya.SlopProbability", ignore):
            findings.append(Finding(
                rule_id="laya.SlopProbability", engine="laya", severity="suggestion",
                line=1, col=1, span=(0, len(text)), match="",
                message=f"Laya rates the whole text as slop with p={advice['noul']:.2f} (advisory only)",
            ))
    else:
        engines["laya"] = {"status": "off"}

    findings.sort(key=lambda f: (f.span[0], f.span[1], f.rule_id))
    return {
        "version": __version__,
        "score": score,
        "findings": [f.as_dict() for f in findings],
        "engines": engines,
    }


def exit_code(report):
    """1 when anything but advisory Laya output was found, else 0."""
    return 1 if any(f["engine"] != "laya" for f in report["findings"]) else 0


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
    density of our TextLint findings when that is higher. Mixed: word-weighted average."""
    en_words, ru_words = _words(masked, en_paras), _words(masked, ru_paras)
    en = _density_score([f for f in findings if not _in(ru_paras, f.span[0])], en_words)
    ours_in_ru = [f for f in findings if f.rule_id.startswith("TextLint.") and _in(ru_paras, f.span[0])]
    ru = max(aiwru_score, _density_score(ours_in_ru, ru_words))
    total = en_words + ru_words
    return round((en * en_words + ru * ru_words) / total) if total else 0
