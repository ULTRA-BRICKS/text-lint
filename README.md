# text-lint

`text-lint check` finds the wording that makes English and Russian prose read machine-written:
stock phrases, flattery, hedging, agent status reports, closing offers. Run it on an issue
body, a PR description, release notes or a Telegram report before you post it. It works
offline: nothing goes to the network, and the Laya classifier every profile asks is a local
socket.

```sh
text-lint check --profile issue body.md
echo "Great question! It's important to note that this robust solution..." | text-lint check
printf 'Стоит отметить, что в современном мире важно понимать...' | text-lint check --format json
```

```text
<stdin>:1:1 TextLint.Sycophancy Flattery: 'Great question'. Delete it and start with the content.
<stdin>:1:17 ai-tells.HedgingPhrases AI hedge: 'It's important to note that'. Delete this throat-clearing and state your point directly.
<stdin>:1:50 ai-tells.OverusedVocabulary AI vocabulary: 'robust'. Replace with a more specific or common word.
score: 100/100, 3 findings, verdict: block (profile prose); aiw-ru skipped (no Russian text)
```

text-lint was called slop-check until 0.2.0 (repo `slop-cli`, package `slop_cli`). The
`slop-check` command still works for now: it prints a deprecation line to stderr and runs
`text-lint check` with its 0.1 arguments translated: `--context` becomes a profile (none or
`docs` → `prose`, `issue`, `pr`, `tg` → `tg-bot`) with `--fail-on suggestion`, so any
finding exits 1; `--laya` is ignored; `--ignore SlopCheck.X` becomes `TextLint.X`. Through
`tg-bot`, `--context tg` now reports a missing Vale or aiw-ru as skipped instead of exit 2, and
no longer flags emoji (`TextLint.DoneEmoji`).

It wraps two existing open-source checkers and adds a rule pack of its own
([ADR 0001](docs/adr/0001-adopt-vale-ai-tells-and-aiw-ru.md) records why these two):

| Engine | Covers | Rules |
|---|---|---|
| [Vale](https://vale.sh) 3.x | English, plus our pack for both languages | [vale-ai-tells](https://github.com/tbhb/vale-ai-tells) v1.37.0 (`ai-tells`, `ai-tells-commits`), vendored; `TextLint` (ours) |
| [aiw-ru](https://pypi.org/project/aiw-ru/) 2.5.x | Russian | its built-in lexicon, context profiles and a 0-100 score |

## Install

```bash
brew install vale                                        # the English engine (a Go binary)
uv tool install git+https://git.hyperide.ai/ultrabricks/text-lint   # text-lint + aiw-ru
# or: pipx install git+https://git.hyperide.ai/ultrabricks/text-lint
# or, to hack on it: git clone https://git.hyperide.ai/ultrabricks/text-lint && cd text-lint && ./install.sh
```

**Via rig:** clone into `~/dev/text-lint` and list it under `tools.items` in
`~/.config/rig/config.yaml`; `rig apply` runs `install.sh`, installs Vale with brew and keeps
the clone fresh. `install.sh` removes an old `hyper-slop-check` install and ends with
`text-lint install-skill`, which writes the agent blurb `~/.agents/skills/.blurbs/text-lint.md`
and deletes the 0.1 blurb `slop-check.md`; rig reads `.blurbs/<item name>.md` as "installed",
so later applies skip the installer:

```yaml
tools:
  items:
    text-lint: {repo: ~/dev/text-lint, brew: [vale]}
```

**Upgrading from slop-check 0.1:** the 0.1 editable install imports `slop_cli`, which no
longer exists, so `slop-check` fails after the pull until `install.sh` runs again. Renaming
the rig item from `slop-check` to `text-lint` makes `rig apply` run it, because
`.blurbs/text-lint.md` is not there yet; without rig, run `./install.sh` in the clone.

aiw-ru, PyYAML and jsonschema are Python dependencies, so the pipx / uv install brings them
along. The symlink fallback of `install.sh` (no pipx, no uv) needs them installed by hand:
`pip install --user 'aiw-ru>=2.5,<3' 'PyYAML>=6,<7' 'jsonschema>=4,<5'`.
`TEXT_LINT_VALE` and `TEXT_LINT_AIWRU` point text-lint at a specific engine binary (the 0.1
names `SLOP_CHECK_VALE` and `SLOP_CHECK_AIWRU` still work while the `slop-check` shim exists).

## Python API

```python
from text_lint import InputDocument, TextLintError, check

report = check(document, profile="tg-bot", enforce="block", dict_path="DICT.json")
```

`check(text, *, profile="prose", lang="auto", enforce="block",
laya_mode="required", dict_path=None)` accepts a string or an input@1 dictionary and
returns a report@1 dictionary. `TextLintError` covers invalid input, configuration and
required-engine failures. The function neither prints nor exits. Strings use the profile's
markup; documents retain their fields, gating flags and sender metadata.

Bot sends use `tg-bot`, owner sends use `as-user`, and voice replies use `spoken`.
The latter two reject emoji and bot formatting; a Laya first-person probability below
0.5 also blocks. `spoken` adds markup and URL warnings. A missing native pack is an error.

Authored sends fail when Laya cannot answer every requested question. Automated sends
report `engines.laya.status = "unreachable"`; the other checks still decide the verdict.
An explicit `TEXT_LINT_LAYA_SOCKET` never starts a service. `laya_mode="ci"` requires
`CI=true`, tries an available socket, and reports `not run` when none answers.

`text-lint selftest` checks packaged native examples and TextLint Vale samples. It uses
fixture probabilities for classifier rules and never contacts Laya or reads a dictionary.

## Usage

```text
text-lint check [PATH|-] [--profile NAME] [--input-json] [--format text|json]
                [--lang auto|en|ru] [--enforce warn|block] [--fail-on error|warning|suggestion]
                [--ignore RULE_ID]... [--laya-mode required|ci] [--dict PATH]
text-lint install-skill
text-lint --version
```

- **Input:** a file, or stdin when `PATH` is `-` or missing, read as the profile's markup
  (Markdown for `prose`). In Markdown, fenced and inline code and URLs are never flagged (a
  finding that touches them is dropped) and do not count toward a paragraph's language or
  the word count.
- **`--input-json`** reads an input@1 document instead
  ([`text_lint/schema/input-v1.json`](text_lint/schema/input-v1.json)): named fields of one
  message, each `plain`, `markdown` or `html`, plus optional `meta`. HTML fields are checked
  with `<pre>`/`<code>` blanked, tags removed and entities decoded; findings point into the
  field as sent. Plain and HTML fields reach Vale as plain text, so markup the reader sees
  literally (`<!-- vale off -->`, backticks) switches nothing off. A field with `"gate":
  false` is reported but never blocks. All fields go to Vale in one call (one file each, so
  one field cannot hide the next) and to aiw-ru in one call.

  ```json
  {"schema": "text-lint/input@1",
   "fields": [{"name": "title", "text": "…", "format": "plain"},
              {"name": "body", "text": "…", "format": "html"}],
   "meta": {"tag": "decision", "agent_authored": true, "send_format": "html"}}
  ```

- **`--context`** is a compatibility alias: `docs` → `prose`, `tg` → `tg-bot`,
  with `issue` and `pr` unchanged. Use either `--context` or `--profile`.
- **`--profile`** (default `prose`) names the kind of text; each is a file in
  [`text_lint/profiles/`](text_lint/profiles/):

  | profile | for | Vale styles | aiw-ru `--context` | Laya | `fail_on` |
  |---|---|---|---|---|---|
  | `prose` | docs, READMEs, ADRs | ai-tells, TextLint | docs | slop | warning |
  | `issue` | ticket titles and bodies | ai-tells, TextLint | technical | slop | warning |
  | `pr` | PR title and body | ai-tells, **ai-tells-commits**, TextLint | docs | slop | warning |
  | `commit` | commit messages | ai-tells-commits, TextLint | technical | slop | warning |
  | `tg-bot` | messages the bot sends | ai-tells, TextLint; emoji rules off | telegram | asks_reader, slop | error |
  | `as-user` | text sent as the owner | ai-tells, TextLint | chat | slop, first_person | error |
  | `spoken` | text spoken as the owner | ai-tells, TextLint | chat | slop, first_person | error |
  | `chat` | agent replies | none | none | slop | error |
  | `ui-strings` | strings from code and locale files | TextLint | chat | slop | warning |

  In `tg-bot`, `as-user` and `spoken`, a missing Vale or aiw-ru is reported as skipped
  instead of exit 2, and their findings are capped at `warning`. `tg-bot` also runs the
  message rules on the message body (the `body` field, else `caption`, else the one unit of a
  file or stdin): `Format.Decision` for `meta.tag: decision` (every sender; needs
  `meta.send_format: html`), `Format.Problem` / `Format.Report` and the intent classifier
  (`Intent.MalformedDecision`, `Intent.MalformedProblem`, `Intent.MalformedReport`,
  `Intent.Mixed`, and the report's `intent` object) for agent-authored or automated messages,
  and the `Structure.LongUnstructured` advisory, all ported from tg-cli.
- **`--lang auto`** (default) splits the text into paragraphs and routes each one by its share
  of Cyrillic letters: Russian paragraphs go to aiw-ru, English ones to the ai-tells rules,
  and the TextLint pack runs on both. Mixed text runs both engines, each on its own part:
  aiw-ru reads every paragraph with Russian in it, and in a Russian paragraph an ai-tells
  finding stays only when its match is English. `--lang en` / `--lang ru` treat the whole
  text as one language.
- **`--enforce`** (default `block`): `block` exits 1 when a finding is at or above
  `fail_on`; `warn` reports and exits 0. **`--fail-on`** overrides the profile's `fail_on`.
- **`--ignore RULE_ID`** drops one rule (`ai-tells.EmDashUsage`) or a whole style
  (`ai-tells`); repeatable.
- **`--laya-mode ci`** is for CI runners (accepted only with `CI=true`, else exit 2): Laya runs
  when a socket answers, and is otherwise reported as `laya: not run (ci runner, no local
  model)`; it never starts a server.
- **`--dict PATH`** names the owner's dictionary, a DICT.json version 1 file
  (`{"version": 1, "rules": [{"id", "pattern", "flags", "replacement", "why",
  "only_if_cyrillic"}]}`). Without it text-lint reads `TEXT_LINT_DICT`, then `TG_DICT_PATH`,
  then `~/.claude/DICT.json`. Each rule becomes `Dict.<id>` (severity `error`, its
  `replacement` as the suggestion) in every profile. Patterns support character classes,
  groups, fixed-width lookbehind, lookahead and quantifiers with JavaScript flags (`i`,
  `m`, `s`; `g`, `y`, `u`, `d` do not change which matches are reported). JavaScript character
  classes, line anchors and Unicode 17 simple case folds are preserved: `\w` and `\d`
  stay ASCII, with long s and Kelvin sign also matching `\w` under `i`. Property escapes,
  named groups, backreferences, inline modifiers, isolated UTF-16 surrogates and repetition
  counts beyond Python's regex limit fail the load with a configuration error. Quantifying
  an expression that may match empty (for example `(?:|a)*` or `(?:a?)+`) is also unsupported;
  ordinary quantifiers such as `a*`, `a*?` and `(?:ab?)+` remain supported. `\b` keeps
  the source's Unicode letter/number/underscore boundary; `only_if_cyrillic` rules
  run only when the gating fields have Cyrillic in them. They read the text as the reader
  sees it: HTML without `<pre>` and `<code>`, Markdown without code and URLs, plain text as
  written. A broken dictionary, or a named one that is missing, exits 2 with the file in the
  message; a missing `~/.claude/DICT.json` means no `Dict.*` rules.
- **`--format json`** prints a report@1 document
  ([`text_lint/schema/report-v1.json`](text_lint/schema/report-v1.json)):
  `{"schema": "text-lint/report@1", "version", "profile", "enforce", "verdict", "score",
  "findings", "suppressed", "engines"}`. Each finding:

  ```json
  {"rule_id": "TextLint.Sycophancy", "engine": "vale", "severity": "error",
   "line": 1, "col": 1, "span": [0, 14], "match": "Great question",
   "message": "Flattery: 'Great question'. Delete it and start with the content.",
   "suggestion": null, "field": null, "path": null, "key": null}
  ```

  `span` is `[start, end)` in characters of the input as read (a file or stdin loses a leading
  BOM and has its line endings normalised to `\n` first), or of the input@1 field as sent, an
  HTML field included: a match on a decoded entity covers the whole entity. `rule_id` is Vale's
  check name (`ai-tells.*`, `ai-tells-commits.*`, `TextLint.*`), `aiwru.<type>.<rule>` or
  `laya.SlopProbability`. `suggestion` is a replacement where a rule has one (`utilize` →
  `use`) or aiw-ru's hint, else `null`; `field` names the input@1 field. `engines` reports
  each engine's status (`ok`, `skipped` with a reason, `unreachable`, `not run`) and its
  version, score or Laya answers; a `tg-bot` report adds `intent` (`kinds`, `action`, `reason`,
  `follow_ups`, `valid`, `hints`, `laya_p`).
- **Text format:** `path:line:col rule_id message → suggestion` (`path[field]:…` for input@1),
  then a `score:` line with the verdict, then a `laya:` line.

**Verdict:** `block` when `--enforce block` and a finding is at or above `fail_on` (a finding
in a `"gate": false` field never blocks); `warn` when there are findings but none blocks;
`pass` when there are none. `laya.SlopProbability` is reported and never counts.

**Score** (0 to 100, higher means more slop): for English, weighted findings per 100 words
(error 3, warning 2, suggestion 1; 10 points per 100 words is 100). For Russian, aiw-ru's own
score, or that density when our TextLint and native (`Dict.*`) findings make it higher.
Mixed text: the two, weighted by word count. The score is a summary; the exit code depends on
the verdict only.

### Exit codes

| code | meaning |
|---|---|
| 0 | verdict `pass` or `warn` |
| 1 | verdict `block` |
| 2 | usage error, unreadable or invalid input, invalid profile, or a needed engine is missing or failed; the message names the engine and its install command |

aiw-ru is only needed when the text has Russian paragraphs (or `--lang ru`); Vale is needed
whenever its profile uses it, since the TextLint pack runs through it.

### Treating findings

Rewrite the sentence: say the fact, give the number, link the evidence. A finding that is
right for this text (quoting someone, a rule about em dashes in a typography guide) may stay;
say why in the PR or the message. Do not reword a sentence only to get it past a rule.

## Rule packs

The Vale side lives in [`text_lint/packs/vale/`](text_lint/packs/vale/); it ships inside the
package with the native packs, so an installed `text-lint` works offline and never runs `vale sync`:

- `vale.ini` is the config text-lint passes to Vale (`--no-global`, so your own Vale setup does
  not leak in). It turns off the vendored rules that fired mostly on ordinary engineering
  prose in a trial over 75 real PR bodies and 80 human-written READMEs; each one has its
  reason next to it. Summary: punctuation that technical text uses on purpose (semicolons,
  colons, `--`), sentence shapes that carry facts (statements of absence, step lists, counts),
  and engineering idioms the metaphor rules misread (a process dies, a rule fires, a fix ships).
  `ai-tells.SycophancyMarkers`, `UniversalSubject`/`UniversalObject` and `FormalRegister` are
  replaced by narrower TextLint rules.
- `styles/ai-tells/`, `styles/ai-tells-commits/`: tbhb/vale-ai-tells v1.37.0, unmodified, MIT
  (`styles/LICENSE.vale-ai-tells`). Tune them in `vale.ini`, never in their files.
- `styles/TextLint/`: our pack, one rule per file, English and Russian. It covers what the
  two engines miss (the examples are in code spans so this README passes its own check):
  bare `стоит отметить` and `важно понимать / помнить` (aiw-ru only catches them before
  `, что`), praise like `Great catch` and `Отличный улов`, agent status reports
  (`Successfully implemented`, `✅ Done!`, `Успешно реализовано`), `All tests pass` with no
  count or link, closing offers (`Would you like me to`, `Если нужно — могу…`), `Ниже приведён…`,
  vague intensifiers and hedges, and safe swaps (`utilize` → `use`, `данный` → `этот`,
  `в целях` → `чтобы`).

aiw-ru's `styleOnly` advice (канцелярит and IT-jargon notes such as «пайплайн») is dropped:
it is style advice, not an AI tell. Vale's `\b` treats Cyrillic letters as word characters
(checked with Vale 3.24.0), so Russian rules use plain `\b` boundaries.

Native rules live in [`text_lint/packs/native/<pack>/`](text_lint/packs/native/), one YAML file
per rule, checked against [`text_lint/schema/native-rule-v1.json`](text_lint/schema/native-rule-v1.json):
a code-backed `kind`, its `params`, the `profiles` it runs in, and examples it must flag and
pass. The `script` pack (every profile) carries two ports of tg-cli's garbled-token guards.
Both read the field as sent, code included:

- `Script.StrayCjk`: one CJK character with a Latin or Cyrillic letter right after it, as
  in an ideograph stuck into a word. A longer CJK run, a CJK suffix that ends a token, and a
  field that is mostly CJK pass.
- `Script.MixedScript`: a word that switches between Cyrillic and another alphabet at least
  twice (homoglyphs, mojibake). Russian dev slang with one English stem inside passes.

### Adding a rule

1. Find the phrase's family in `text_lint/packs/vale/styles/TextLint/` and add a token there, or
   create `NewRule.yml` with a comment saying what it catches and why. Use
   [`existence`](https://vale.sh/docs/checks/existence) for phrases and
   [`substitution`](https://vale.sh/docs/checks/substitution) with `action: {name: replace}`
   for a safe swap. Russian rule names start with `Ru`.
2. Add a line `NewRule<TAB>text it must flag` to `text_lint/packs/vale/samples.tsv`; the
   integration test fails for a rule file without a sample.
3. Before adding a Russian phrase, check whether aiw-ru already reports it
   (`aiw-ru scan --json`), so the two engines do not report the same span twice.
4. Run `text-lint check` on `tests/fixtures/clean_en.md` and `clean_ru.md`: they must stay clean.

## Laya

Every profile asks [task-cli](https://git.hyperide.ai/ultrabricks/task-cli)'s Laya classifier
the questions in its `questions` (`slop`, `asks_reader`, `first_person`) in one request per
run, about the first 2000 characters of the gating fields. text-lint talks to
`TEXT_LINT_LAYA_SOCKET` when set, else to the highest-version
`$XDG_STATE_HOME/task-cli/classify/laya-*.sock` (default `~/.local/state/...`) that accepts a
connection, and only when that directory is private and yours and no other user can write to a
directory above it (the text you check goes to that socket). When no socket answers, it runs
`task classify-server start` once (task-cli 0.23 or later), waits up to the profile's
`start_wait_ms` (60 s) and sends the request to the socket path the command prints; a socket
named in `TEXT_LINT_LAYA_SOCKET` is never started. Laya still unreachable, or a request that
fails, exits 2 naming Laya; only an automated message (`meta.automated`) carries on, reported
as `laya: unreachable`. The request timeout is the profile's `timeout_ms` (1.5 s), or
`TEXT_LINT_LAYA_TIMEOUT_MS`. The `slop` question is "Is this text AI-generated slop:
buzzwords, stock phrases, hedging, flattery or vague claims with no concrete facts?"; its
answer is printed (`laya: p(slop)=0.32`) and adds a `laya.SlopProbability` suggestion at 0.3
and above, which never counts toward the verdict. In a probe on 2026-10-16 obvious English slop
scored 0.32, Russian slop 0.60, plain technical English 0.0007 and plain Russian 0.09.
`asks_reader` is asked only for an agent's message in which the intent classifier found no
question; at 0.9 or more it adds a hint to `intent.hints`.

## Development

Rules for agents working here: [AGENTS.md](AGENTS.md). Tests: unit tests drive the CLI against
fake engines (`tests/fakes/`), a fake Laya socket and a fake `task` that every test gets
(`tests/conftest.py`); `tests/test_integration.py` runs the real Vale and aiw-ru and is skipped
when either is missing. CI installs both and sets `TEXT_LINT_REQUIRE_ENGINES=1`, which turns
that skip into a failure, then installs the built wheel outside the checkout and checks it.

```sh
uv run --no-project --with 'pytest>=8,<9' --with 'aiw-ru>=2.5,<3' --with 'PyYAML>=6,<7' \
  --with 'jsonschema>=4,<5' pytest tests -q
```

## License

MIT. The vendored vale-ai-tells styles are MIT, © 2025-2026 Tony Burns
(`text_lint/packs/vale/styles/LICENSE.vale-ai-tells`).
