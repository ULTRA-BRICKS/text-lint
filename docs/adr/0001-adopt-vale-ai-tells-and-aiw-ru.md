# ADR 0001: Wrap Vale + vale-ai-tells and aiw-ru instead of writing a checker from scratch

- Status: accepted
- Date: 2026-10-16
- Ticket: [ultrabricks/text-lint#1](https://git.hyperide.ai/ultrabricks/text-lint/issues/1)
  (filed when the repo was called slop-cli)

## Context

The owner asked several times for a local checker for prose, in English and Russian, that
flags the phrasing that makes issues, PR bodies and Telegram reports read machine-written.
Nothing like it existed in our repos: agent-tools has Oxlint rules for code, and tg-cli's
dictionary gate only bans words the owner listed in `~/.claude/DICT.json`. The owner asked us
to adopt or wrap ready open-source tools, add our own English and Russian rules where they
fall short, and record the comparison here.

Requirements:

- runs offline as a CLI, with no LLM call;
- English and Russian;
- machine-readable findings with rule id, line, column and a hint;
- rules kept as data, so new phrases are a data change;
- a permissive license and a project that is still maintained.

## Options considered

Survey date 2026-10-16. Activity is taken from release tags and PyPI/npm publish dates; the
GitHub API rate-limited the survey, so repository push dates were not available for all.

| Tool | Offline CLI | Languages | Rules as data | Findings: JSON / line:col / rule id / hint / score | Exit code on findings | License | Activity |
|---|---|---|---|---|---|---|---|
| **Vale + tbhb/vale-ai-tells** | yes (Go binary) | ai-tells: English; the engine takes any language | yes: 137 YAML rules in `ai-tells`, 15 in `ai-tells-commits`; per-rule on/off and level in `.vale.ini` | yes / yes / yes / only where a rule defines an action / no score | 1 on an error-level alert, 2 on a runtime error | MIT / MIT | Vale 3.24.0; ai-tells v1.37.0, frequent releases |
| **aiw-ru** (ormeilu/avoid-ai-writing-russian) | yes (`aiw-ru scan`) | Russian | no: a Python lexicon (~1250 lines) with P0, P1, P2 severities, context profiles, density thresholds | yes / yes / yes / yes (advice, not a replacement) / 0-100 with a label | 0 unless `--fail-above N` | MIT | 2.5.1, published 2026-10-14; one maintainer |
| humanizer-ru (`ru-humanizer`) | yes | Russian | Python lists (21 hard bans, 67 patterns) | JSON with counts only; positions only through its MCP server | 0 / 1 / 2 | MIT | 3.31.5, 2026-10-16 |
| slop-gate | yes (Node ≥ 18) | English ~40 rules, Russian 10, others | JSON packs | `--json` only on main, npm latest may lack it; no score | 0 / 1 / 2 | MIT | npm 0.5.0, 2026-07-11 |
| sloptrim | git clone only | English | 71 patterns hard-coded in one ~2340-line script | JSON and a score, but no line or column | always 0 | Apache-2.0 | 0.9.4, 2026-10-05 |
| slopless | yes (Node 20+) | English | textlint rules in TypeScript | textlint JSON; no score | 0 / 1 / 2 | MIT | 0.2.41, 2026-10-16 |
| slopotron | no: a prompt-only skill | Russian | ~100 markers as prose | n/a | n/a | MIT | 2026-10-02 |
| reedach | no: a library, HTTP server and widget | Russian style and spelling | JS rules | HTTP JSON with replacements | n/a | MIT | created 2026-10-12 |
| proselint | yes | English | ~70 Python checks | JSON with positions and replacements | not checked | BSD-3 | 0.16.0 |
| write-good, alex | yes | English | JS checks | per-line or vfile output | not checked | MIT | npm last updated 2022 / 2023 |

What disqualified the rest:

- humanizer-ru: its CLI JSON has counts but no positions, so a finding cannot point at a line.
- slop-gate: ten Russian rules, no score, and the JSON output may not be released yet. Its
  line-by-line scan also misses a phrase broken across two lines.
- sloptrim: no line or column, exit 0 whatever it finds, and rules hard-coded in one script.
- slopless: English only, rules in TypeScript, and a Node runtime for one language.
- slopotron and reedach have no CLI that fits; proselint, write-good and alex do not target
  AI tells (write-good and alex are also unmaintained).

## Decision

`slop-check` (renamed text-lint in 0.2.0, with its `SlopCheck` style renamed `TextLint`) is a
thin Python wrapper around two engines with one merged output:

- **English: Vale with vale-ai-tells v1.37.0**, vendored into `text_lint/packs/vale/styles/` so an
  install works offline and never runs `vale sync`. `ai-tells-commits` is added for PR bodies.
- **Russian: aiw-ru**, a Python dependency of the package, called as
  `aiw-ru scan --json --context …` on the Russian paragraphs only.
- **Our own `TextLint` Vale style**: YAML files for both languages that cover the gaps the
  survey found (below). Vale's `\b` treats Cyrillic letters as word characters (checked with
  a real Vale 3.24.0 run), so Russian rules use ordinary `\b`.
- The wrapper owns everything the engines disagree on: paragraph routing by Cyrillic share,
  dropping anything reported inside code or a URL, one finding shape, exit codes (0 / 1 / 2),
  and a 0-100 score (aiw-ru's for Russian, findings per 100 words for English, which Vale has
  no score for).

Gaps filled by `TextLint` (examples in code spans, so this ADR passes its own check):

- bare `стоит отметить` and `важно понимать / помнить`: aiw-ru only matches them before `, что`;
- English praise ai-tells lacks: `Great catch`, `Good catch`, `Nice find`;
- agent status reports, in neither tool: `Successfully implemented`, `✅ Done!`,
  `Успешно реализовано`, `Готово! ✅`, `All tests pass` with no count or link;
- closing offers (`Would you like me to`, `Если нужно — могу…`) and `Ниже приведён…`;
- vague intensifiers and hedges ai-tells does not list;
- safe swaps that ship a replacement: `utilize` → `use`, `данный` → `этот`, `в целях` → `чтобы`.

Tuning: a trial over 75 PR bodies from our repos and 80 human-written READMEs showed which
ai-tells rules fire mostly on ordinary engineering prose. Those are disabled in `vale.ini`,
each with its reason: semicolons, colons and `--`; statements of absence and step lists;
counts before a list; idioms like "the process dies" or "the rule fires". The bare
"Absolutely", "Certainly" and "Of course" of `SycophancyMarkers` are dropped. Vale's em-dash
rule is dropped on Cyrillic lines, because the em dash is normal Russian punctuation.

## Consequences

- Two runtimes: Vale is a Go binary installed separately (`brew install vale`, or rig's
  `brew: [vale]`); aiw-ru needs Python ≥ 3.12, so text-lint does too.
- Upgrading vale-ai-tells means replacing the vendored directory and re-running the trial; a
  new upstream rule is on by default until someone disables it with a reason.
- The two engines can report the same phrase. Russian TextLint rules use a lookahead to leave
  the «, что» forms to aiw-ru, and the integration test fails on a duplicated span.
- aiw-ru's lexicon is code, not data; Russian phrases it misses go into `TextLint`, not into
  a fork of aiw-ru.
- English and Russian scores come from different methods, so a mixed text's score is a rough
  summary. The exit code depends on findings only.
- Rejected fallback: Vale only, with the Russian rules ported from humanizer-ru, aiw-ru and
  slop-gate (all MIT). It would mean one binary, but it loses aiw-ru's context profiles,
  density thresholds and calibrated score.
