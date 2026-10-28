"""The message formats and the `format` kind (Format.Decision, Format.Problem, Format.Report).

Ports of tg-cli's validators, keeping their bilingual detection and their messages:
`validate_decision` is `validateEscalationFormat` (features/cli/escalation-format.ts:299),
`validate_problem` and `validate_report` are `validateProblemFormat` and
`validateReportFormat` (features/cli/intent-gate.ts:266, :298). They read the raw message,
because they look for the HTML structure itself (`<h3>` sections, `<hr>`, lists).

The kind (``params.validator``) runs on the message unit when ``meta.tag`` names it:
- `decision` applies to every sender and also requires the sender's explicit
  ``meta.send_format`` to be `html` (tg-cli's `escalationFormatGate`, args.ts:282-285), except
  for a `--table` send, whose final body tg-cli rechecks without it (tg:1653-1656);
- `problem` and `report` are what the intent classifier (intent.py) reports as a malformed
  problem or report for a message tagged so: they run only for agent-authored messages and
  are skipped for automated ones, as tg-cli runs them inside `classifyIntent`.
"""

import re
from dataclasses import replace

from ..text import _decode_entity
from . import finding
from .jsre import LN, WE, B, L, W, js_len

# A recommendation: "which option I'd pick and why". Bilingual.
_RECOMMENDATION = (
    rf"{B}(?:recommend|recommendation|i (?:suggest|propose)|my pick|i'?d pick|i would (?:pick|go with)"
    rf"|lean(?:ing)? toward){B}|рекоменд|предлаг|советую|склоня|выбрал бы|мой выбор|остановил бы|пошёл бы|пошел бы"
)
RECOMMENDATION_RE = re.compile(_RECOMMENDATION, re.IGNORECASE)
# Pros/cons / tradeoffs attached to the options. Bilingual.
PROS_CONS_RE = re.compile(
    rf"{B}(?:pros?|cons?|trade[- ]?offs?|advantages?|disadvantages?|upsides?|downsides?|benefits?|drawbacks?){B}"
    r"|плюс|минус|компромисс|преимуществ|недостат|за и против|риск",
    re.IGNORECASE,
)
# A concrete `file:line` pointer, matched per token from its start (escalation-format.ts:63).
_FILE_REF_TOKEN_RE = re.compile(
    r"(?:\.{0,2}/)?(?:(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*[A-Za-z0-9_]"
    r"|[A-Za-z0-9_.-]*\.(?:ts|tsx|js|jsx|mjs|cjs|py|go|rs|json|ya?ml|md|sh|sql|toml|css|scss|html?|vue|svelte"
    r"|c|cc|cpp|h|hpp|java|rb|php|swift|kt|lua|zig|ex|exs)"
    r"|Makefile|Dockerfile|Containerfile|Justfile|Rakefile|Gemfile|Procfile)(?::[0-9]+|#L[0-9]+)(?![0-9])",
    re.IGNORECASE,
)
_TOKEN_SPLIT_RE = re.compile(r"[\s<>\"'`()\[\]{},;]+")
_URL_PREFIX_RE = re.compile(r"^[a-z][A-Za-z0-9_+.-]*://[^/]*/?", re.IGNORECASE)

LIST_RE = re.compile(r"<(?:ul|ol|li)" + B, re.IGNORECASE)
HEADING_RE = re.compile(r"<h[1-6]" + B, re.IGNORECASE)
HR_RE = re.compile(r"<hr" + B, re.IGNORECASE)

_SECTION_HEADING_RE = re.compile(rf"<h([1-6]){B}[^>]*>([\s\S]*?)</h\1\s*>|^[ \t]*#{{1,6}}[ \t]+(.+)$", re.IGNORECASE | re.MULTILINE)
_FENCED_CODE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[\s\S]*?(?:^ {0,3}\1|\Z)", re.MULTILINE)
# An Options section by name: it wins over the non-options names below.
OPTIONS_HEADING_RE = re.compile(rf"{B}(?:options?|alternatives?|choices?){B}|вариант|опци|альтернатив", re.IGNORECASE)
# Sections whose bullets and tables are not the options.
NON_OPTIONS_HEADING_RE = re.compile(
    _RECOMMENDATION + r"|where to look|references?|links?|context|background|glossary|terms"
    r"|где смотреть|ссылк|контекст|глоссари|термин",
    re.IGNORECASE,
)
_GLOSSARY_HEADING_RE = re.compile(rf"glossary|{B}terms{B}|глоссари|термин|словарик", re.IGNORECASE)
_GLOSSARY_LABEL_RE = re.compile(rf"(?:glossary|глоссарий|термины)\s*[:：—–-]\s*{LN}", re.IGNORECASE)
_RECOMMENDATION_LABEL_WORD_RE = re.compile(r"^(?:recommendations?|рекомендаци[яи]|my|our|моя|мои|наша|наши)[:.]?\Z",
                                           re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]*>")
HAS_CONTENT_RE = re.compile(LN)

_CODE_ELEMENT_RE = re.compile(rf"<(pre|code){B}([^>]*)>([\s\S]*?)</\1\s*>", re.IGNORECASE)

_WALL_LINE_CHARS = 350
_INLINE_ENUM_RE = re.compile(
    r"(?:pros?|cons?|плюсы|минусы|преимуществ[A-Za-z0-9_]*|недостат[A-Za-z0-9_]*)\s*[:：].*?,.*?,.*?,", re.IGNORECASE)
_BLOCK_TAGS = ("h[1-6]|p|tr|li|table|caption|thead|tbody|tfoot|ul|ol|blockquote|pre|details|summary|aside|footer"
               "|figure|figcaption|tg-math-block")
_OPEN_BLOCK_RE = re.compile(rf"<(?:{_BLOCK_TAGS}){B}[^>]*>", re.IGNORECASE)
_CLOSE_BLOCK_RE = re.compile(rf"</(?:{_BLOCK_TAGS})\s*>|<(?:hr|br){B}[^>]*/?>", re.IGNORECASE)
# A visual line that is table/list/code/quote markup, not the sender's prose.
_MARKUP_LINE_RE = re.compile(
    rf"^</?(?:table|tr|td|th|thead|tbody|tfoot|caption|ul|ol|li|pre|code|blockquote|aside|tg-math-block){B}", re.IGNORECASE)
_BOXED_GLYPH_RE = re.compile("[│┌┬┐└┴┘├┼┤─]")
_ENTITY_RUN_RE = re.compile(r"&(?:#[0-9]+|#x[0-9a-f]+|[A-Za-z0-9_]+);", re.IGNORECASE)

# What validate_decision lists as missing, in the skill's order (escalation-format.ts:299-384).
MISSING_OPTIONS = ("Options as a real table (<table> / markdown pipe grid / tg --table) or a <ul>/<ol> list "
                   "of >=2 options")
MISSING_PROS_CONS = 'Pros/cons (tradeoffs) for each option — "плюсы/минусы", pros/cons'
MISSING_RECOMMENDATION = "A recommendation (which option you’d pick and why) — \"рекомендация\""
MISSING_WHERE = 'Where to look — a concrete file:line reference (e.g. features/foo.ts:42) — "где смотреть"'
MISSING_CONTEXT = "Context — a sentence of prose on where the code is and what it does"
MISSING_GLOSSARY = ("Glossary — every internal name (ticket, module, flag, acronym) explained in one phrase "
                    "— \"глоссарий\"")
MISSING_HEADINGS = "Structure — put each section under its own <h3>/<h4> heading (needs --format html; ≥2 headings)"
MISSING_LISTS = ("Structure — use <ul>/<ol> bullet lists for pros/cons and enumerations (one short item per "
                 "<li>, never inline \"плюсы: a, b, c\")")
MISSING_HR = "Structure — separate sections with <hr> dividers"
MISSING_READABILITY = ("Readability — break run-on paragraphs: short lines, one idea per bullet (no long dense "
                       "paragraph, no inline comma-run enumeration)")
# Prepended when the sender did not pass --format html (args.ts:282-285).
MISSING_HTML = ("Send as a Rich Message: pass --format html so the headings/table/list render (raw tags are "
                "literal text under plain format)")


def has_file_ref(text):
    return any(_FILE_REF_TOKEN_RE.match(_URL_PREFIX_RE.sub("", token, count=1))
               for token in _TOKEN_SPLIT_RE.split(text))


def _mask_code_contents(text, mask):
    """``text`` with the contents of every <pre>/<code> element rewritten by ``mask``."""
    def repl(m):
        tag, attrs, inner = m.group(1), m.group(2), m.group(3)
        opening = f"<{tag}{attrs}>"
        whole = m.group(0)
        return opening + mask(inner) + whole[len(opening) + len(inner):]
    return _CODE_ELEMENT_RE.sub(repl, text)


def _mask_angles(code):
    return code.replace("<", "‹").replace(">", "›")


def split_sections(text):
    """[(heading, body)]: a heading (`<h1>`..`<h6>` or a Markdown `#` line outside code)
    opens a section that runs to the next one; text before the first is a section with an
    empty heading."""
    def neutralize(code):
        return re.sub(r"^([ \t]*)#", "\\1＃", _mask_angles(code), flags=re.MULTILINE)

    scanned = _FENCED_CODE_RE.sub(lambda m: neutralize(m.group(0)), _mask_code_contents(text, neutralize))
    sections = []
    heading = ""
    start = 0
    for m in _SECTION_HEADING_RE.finditer(scanned):
        sections.append((heading, text[start:m.start()]))
        raw = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        heading = TAG_RE.sub("", raw).strip()
        start = m.end()
    sections.append((heading, text[start:]))
    return sections


def _cells(row):
    return [TAG_RE.sub("", c.group(2)).strip() for c in re.finditer(r"<(t[hd])\b[^>]*>([\s\S]*?)</\1\s*>", row, re.IGNORECASE)]


def table_option_rows(body):
    """The option rows of every HTML table and Markdown pipe grid in ``body``; header rows
    (all `<th>`, or the grid line above `| --- |`) are dropped.

    Like the TypeScript reference, every separator-looking line opens a grid, so a
    dashes-only data row inside a grid also opens a shorter one and the decision count
    follows. Each source line is split once: overlapping grids share their row lists, so
    callers must not mutate them.
    """
    tables = []
    for table in re.finditer(r"<table\b[\s\S]*?</table\s*>", body, re.IGNORECASE):
        rows = [m.group(0) for m in re.finditer(r"<tr\b[\s\S]*?</tr\s*>", table.group(0), re.IGNORECASE)]
        tables.append([_cells(row) for row in rows if re.search(r"<td\b", row, re.IGNORECASE)])
    lines = [line.strip() for line in body.split("\n")]
    run_end = [len(lines)] * (len(lines) + 1)  # The first line at or after k without a pipe.
    for k in range(len(lines) - 1, -1, -1):
        run_end[k] = run_end[k + 1] if "|" in lines[k] else k
    cells = [None] * len(lines)
    parsed = 0
    for i in range(1, len(lines)):
        if not ("|" in lines[i] and "-" in lines[i] and re.fullmatch(r"[\s|:-]+", lines[i])):
            continue
        end = run_end[i + 1]
        for j in range(max(i + 1, parsed), end):
            cells[j] = [c.strip() for c in re.sub(r"^\||\|\Z", "", lines[j]).split("|")]
        parsed = max(parsed, end)
        tables.append(cells[i + 1:end])
    return tables


def boxed_option_rows(body):
    """The option rows of a boxed `tg --table` grid (a `<pre>` of `│` rows; the first is the
    header)."""
    return [[[c.strip() for c in line.split("│")[1:-1]] for line in pre.group(0).split("\n") if "│" in line][1:]
            for pre in re.finditer(r"<pre\b[^>]*>[^<]*[┌└][\s\S]*?</pre\s*>", body, re.IGNORECASE)]


def top_level_list_item_counts(body):
    """The item count of every top-level <ul>/<ol> (a nested sub-list adds no item)."""
    counts = []
    depth = 0
    for m in re.finditer(r"<(/?)(ul|ol|li)\b[^>]*>", body, re.IGNORECASE):
        close, tag = m.group(1), m.group(2).lower()
        if tag != "li" and not close:
            if depth == 0:
                counts.append(0)
            depth += 1
        elif tag != "li":
            depth = max(0, depth - 1)
        elif not close and depth == 1:
            counts[-1] += 1
    return counts


def _visual_lines(body):
    """``body`` split where Telegram breaks the line: block tags count as line breaks, and a
    whole <pre>/<code> element stays on the line its opening tag starts."""
    text = _mask_code_contents(body, lambda inner: re.sub(r"\r?\n", " ", _mask_angles(inner)))
    text = _OPEN_BLOCK_RE.sub(lambda m: "\n" + m.group(0), text)
    text = _CLOSE_BLOCK_RE.sub(lambda m: m.group(0) + "\n", text)
    return [line.strip() for line in text.split("\n")]


def _markup_line(line):
    return "|" in line or _MARKUP_LINE_RE.search(line) or _BOXED_GLYPH_RE.search(line)


def has_wall_of_text(body):
    """A line of more than 350 visible characters, or an inline comma-run after a pros/cons
    label, outside table, list, code and quote markup."""
    for line in _visual_lines(body):
        if _INLINE_ENUM_RE.search(line):
            return True
        if _markup_line(line):
            continue
        visible = _ENTITY_RUN_RE.sub("&", TAG_RE.sub("", line))
        if js_len(visible) > _WALL_LINE_CHARS:
            return True
    return False


def has_context_prose(body):
    """A line outside table and list markup with at least 20 letters or digits."""
    for line in _visual_lines(body):
        if not line or _markup_line(line):
            continue
        if len(re.findall(LN, line)) >= 20:
            return True
    return False


def _option_tables(body):
    return [rows for rows in table_option_rows(body) + boxed_option_rows(body) if len(rows) >= 2]


def validate_decision(body):
    """What a decision body lacks of the decision-request format, in the skill's order; [] when
    it is complete (validateEscalationFormat)."""
    missing = []
    text = body or ""
    sections = split_sections(text)
    named = [s for s in sections if OPTIONS_HEADING_RE.search(s[0])]
    option_sections = named or [s for s in sections if not NON_OPTIONS_HEADING_RE.search(s[0])]
    tables = [rows for _, body_ in option_sections for rows in _option_tables(body_)]
    has_options_list = any(n >= 2 for _, body_ in option_sections for n in top_level_list_item_counts(body_))
    if not tables and not has_options_list:
        missing.append(MISSING_OPTIONS)
    tradeoffs_filled = has_options_list or all(
        len([c for c in cells if c]) >= 2 for rows in tables for cells in rows)
    if not PROS_CONS_RE.search(text) or not tradeoffs_filled:
        missing.append(MISSING_PROS_CONS)
    has_recommendation = any(RECOMMENDATION_RE.search(b) for _, b in sections) or any(
        RECOMMENDATION_RE.search(h) and (
            HAS_CONTENT_RE.search(TAG_RE.sub("", b))
            or any(HAS_CONTENT_RE.search(w) and not _RECOMMENDATION_LABEL_WORD_RE.search(w) for w in h.split()))
        for h, b in sections)
    if not has_recommendation:
        missing.append(MISSING_RECOMMENDATION)
    if not has_file_ref(text):
        missing.append(MISSING_WHERE)
    if not has_context_prose(text):
        missing.append(MISSING_CONTEXT)
    has_glossary = any(_GLOSSARY_HEADING_RE.search(h) and HAS_CONTENT_RE.search(TAG_RE.sub("", b))
                       for h, b in sections) or _GLOSSARY_LABEL_RE.search(TAG_RE.sub(" ", text))
    if not has_glossary:
        missing.append(MISSING_GLOSSARY)
    if len(HEADING_RE.findall(text)) < 2:
        missing.append(MISSING_HEADINGS)
    if not LIST_RE.search(text):
        missing.append(MISSING_LISTS)
    if not HR_RE.search(text):
        missing.append(MISSING_HR)
    if has_wall_of_text(text):
        missing.append(MISSING_READABILITY)
    return missing


_TOPIC_RE = re.compile(r"^(?:options?|alternatives?|choices?|варианты?|опции|альтернативы)[:.]?\Z", re.IGNORECASE)


def decision_labels(body):
    """One label per separate decision in a body that passes validate_decision: per named
    Options section with a real option structure, else per option table; at least one."""
    sections = split_sections(body or "")

    def topic(i):
        own = sections[i][0]
        if not _TOPIC_RE.search(own):
            return own
        before = sections[i - 1][0] if i > 0 else ""
        return before if before and not NON_OPTIONS_HEADING_RE.search(before) else ""

    named = [topic(i) for i, (h, b) in enumerate(sections)
             if OPTIONS_HEADING_RE.search(h) and (_option_tables(b) or any(n >= 2 for n in top_level_list_item_counts(b)))]
    labels = named or [topic(i) for i, (h, b) in enumerate(sections)
                       if not NON_OPTIONS_HEADING_RE.search(h) for _ in _option_tables(b)]
    if not labels:
        return ["decision 1"]
    return [f"decision {i}: {label}" if label else f"decision {i}" for i, label in enumerate(labels, 1)]


# --- problem and report (intent-gate.ts:242-318) ---

# What the sender needs from the human to get unblocked (also how intent.py tells a problem's
# own request from a choice).
NEED_RE = re.compile(
    rf"{W}(?:нуж(?:ен|на|но|ны)|прошу|просьба|требуется|дай|дайте|дашь|дадите|выдай|выдайте|выдашь|выдадите"
    r"|предоставь|предоставьте|предоставишь|разреши|разрешите|разрешишь|разблокируй|сделай|сделайте|сделаешь"
    rf"|добавь|добавьте|добавишь|можешь|можете|сможешь|сможете){WE}"
    rf"|{B}(?:need|please|requires? your|grant|unblock|could you|can you){B}",
    re.IGNORECASE,
)
# The problem format's evidence: a link, code, a tg# reference or a local path.
_LINK_OR_LOG_RE = re.compile(
    rf"{B}[a-z][a-z0-9+.-]*://\S+|<(?:pre|code){B}|`[^`\n]+`|tg#[0-9]+"
    r"|(?:^|\s)(?:~|\.{0,2})/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    rf"(?<![^\W_])(?<![&#])#[0-9]+|{B}[A-Z][A-Z0-9]{{1,9}}-[0-9]+{B}|{B}https?://\S+|{B}tg#[0-9]+")
_VERIFY_RE = re.compile(
    rf"{W}(?:тест{L}*|провер{L}*|зел[её]н{L}*|прош[её]л|прошли|прошло|сборк{L}*|ревью|линт{L}*){WE}"
    rf"|{B}(?:tests?|CI|green|pass(?:ed|es|ing)?|verif[A-Za-z0-9_]*|smoke|e2e|review(?:ed)?|lint|typecheck|exit 0){B}"
    rf"|<pre{B}",
    re.IGNORECASE,
)
_LONG_REPORT_CHARS = 500
_MARKDOWN_LIST_LINE_RE = re.compile(r"^\s*(?:[-•*]|[0-9]+[.)])\s+\S")
_ENTITY_RE = re.compile(r"&(#[0-9]+|#x[0-9a-f]+|[a-z]+);", re.IGNORECASE)


def decode_entities(text):
    return _ENTITY_RE.sub(_decode_entity, text)


def validate_problem(body):
    """What a problem body lacks of the problem format (validateProblemFormat): context,
    evidence, the ask, and the decision format's structure."""
    text = body or ""
    missing = []
    if not has_context_prose(text):
        missing.append("Context — a sentence of prose: what you were doing and what is blocked")
    if not has_file_ref(text) and not _LINK_OR_LOG_RE.search(text):
        missing.append("Evidence / where to look — a file:line, a link, a log path, or the failing "
                       "command/output in <code>/<pre>")
    if not NEED_RE.search(TAG_RE.sub(" ", text)):
        missing.append("The ask — what exactly you need from the human to get unblocked "
                       "(\"нужен доступ к …\", \"please grant …\")")
    if len(HEADING_RE.findall(text)) < 2:
        missing.append("Structure — each section under its own <h3>/<h4> heading (needs --format html; ≥2 headings)")
    if not HR_RE.search(text):
        missing.append("Structure — separate sections with <hr> dividers")
    if has_wall_of_text(text):
        missing.append("Readability — short lines, one idea per bullet (no long dense paragraph)")
    return missing


def validate_report(body):
    """What a report body lacks of the report format (validateReportFormat): what it is about,
    proof it was verified, and visible structure once it is long."""
    text = body or ""
    visible = decode_entities(TAG_RE.sub("", text))
    missing = []
    if not _REFERENCE_RE.search(visible) and not re.search(r"<a\s[^>]*href=", text, re.IGNORECASE):
        missing.append("Reference — the ticket / PR / commit link the report is about (#123, ABC-123 or a URL)")
    if not _VERIFY_RE.search(text):
        missing.append("Verification — what you ran or checked and its result (tests / CI / live check)")
    list_lines = sum(1 for line in visible.split("\n") if _MARKDOWN_LIST_LINE_RE.search(line))
    structured = len(HEADING_RE.findall(text)) >= 2 or re.search(r"<(?:ul|ol|li)\b", text, re.IGNORECASE) or list_lines >= 2
    if js_len(visible) > _LONG_REPORT_CHARS and not structured:
        missing.append(f"Structure — a report over {_LONG_REPORT_CHARS} characters needs headings or a list")
    return missing


def run(rule, unit, ctx):
    """The `format` kind: one finding on the message unit, naming every missing part."""
    validator = rule.params["validator"]
    meta = ctx.meta
    if unit is not ctx.message or meta.tag != validator:
        return []
    if validator == "decision":
        missing = validate_decision(unit.semantic)
        if meta.send_format != "html" and not meta.table:
            missing.insert(0, MISSING_HTML)
    elif ctx.intent.action == "block" and ctx.intent.reason == f"malformed-{validator}":
        missing = ctx.intent.detail
    else:
        missing = []
    if not missing:
        return []
    hit = finding(rule, unit, 0, 0, space="semantic", match="")
    return [replace(hit, message=hit.message + " Missing:" + "".join(f" • {item}" for item in missing))]
