# slop-cli

`slop-check` finds the wording that makes English and Russian prose read machine-written:
stock phrases, flattery, hedging, agent status reports, closing offers. Run it on an issue
body, a PR description, release notes or a Telegram report before you post it. It works
offline: no model call unless you ask for the optional Laya score.

```sh
slop-check --context issue body.md
echo "Great question! It's important to note that this robust solution..." | slop-check
printf 'Стоит отметить, что в современном мире важно понимать...' | slop-check --format json
```

```text
<stdin>:1:1 SlopCheck.Sycophancy Flattery: 'Great question'. Delete it and start with the content.
<stdin>:1:17 ai-tells.HedgingPhrases AI hedge: 'It's important to note that'. Delete this throat-clearing and state your point directly.
<stdin>:1:50 ai-tells.OverusedVocabulary AI vocabulary: 'robust'. Replace with a more specific or common word.
score: 100/100, 3 findings; aiw-ru skipped (no Russian text)
```

It wraps two existing open-source checkers and adds a rule pack of its own
([ADR 0001](docs/adr/0001-adopt-vale-ai-tells-and-aiw-ru.md) records why these two):

| Engine | Covers | Rules |
|---|---|---|
| [Vale](https://vale.sh) 3.x | English, plus our pack for both languages | [vale-ai-tells](https://github.com/tbhb/vale-ai-tells) v1.37.0 (`ai-tells`, `ai-tells-commits`), vendored; `SlopCheck` (ours) |
| [aiw-ru](https://pypi.org/project/aiw-ru/) 2.5.x | Russian | its built-in lexicon, context profiles and a 0-100 score |

## Install

```bash
brew install vale                                        # the English engine (a Go binary)
uv tool install git+https://git.hyperide.ai/ultrabricks/slop-cli   # slop-check + aiw-ru
# or: pipx install git+https://git.hyperide.ai/ultrabricks/slop-cli
# or, to hack on it: git clone https://git.hyperide.ai/ultrabricks/slop-cli && cd slop-cli && ./install.sh
```

**Via rig:** clone into `~/dev/slop-cli` and list it under `tools.items` in
`~/.config/rig/config.yaml`; `rig apply` runs `install.sh`, installs Vale with brew and keeps
the clone fresh. `install.sh` ends with `slop-check install-skill`, which writes the agent blurb
`~/.agents/skills/.blurbs/slop-check.md`; rig reads that file as "installed", so later applies
skip the installer:

```yaml
tools:
  items:
    slop-check: {repo: ~/dev/slop-cli, brew: [vale]}
```

aiw-ru is a Python dependency, so the pipx / uv install brings it along. The symlink
fallback of `install.sh` (no pipx, no uv) needs `aiw-ru` on PATH: `pip install --user 'aiw-ru>=2.5,<3'`.
`SLOP_CHECK_VALE` and `SLOP_CHECK_AIWRU` point slop-check at a specific engine binary.

## Usage

```text
slop-check [PATH|-] [--format text|json] [--lang auto|en|ru] [--context issue|pr|tg|docs]
           [--ignore RULE_ID]... [--laya] [--version]
```

- **Input:** a file, or stdin when `PATH` is `-` or missing. Markdown-aware: fenced and
  inline code and URLs are never flagged (a finding that touches them is dropped) and do not
  count toward a paragraph's language or the word count.
- **`--lang auto`** (default) splits the text into paragraphs and routes each one by its share
  of Cyrillic letters: Russian paragraphs go to aiw-ru, English ones to the ai-tells rules,
  and the SlopCheck pack runs on both. Mixed text runs both engines, each on its own part:
  aiw-ru reads every paragraph with Russian in it, and in a Russian paragraph an ai-tells
  finding stays only when its match is English. `--lang en` / `--lang ru` treat the whole
  text as one language.
- **`--context`** tunes both engines to where the text goes:

  | context | Vale | aiw-ru `--context` |
  |---|---|---|
  | (none) | ai-tells, SlopCheck | general |
  | `issue` | ai-tells, SlopCheck | technical |
  | `pr` | ai-tells, **ai-tells-commits**, SlopCheck | technical |
  | `tg` | ai-tells, SlopCheck | telegram |
  | `docs` | ai-tells, SlopCheck | docs |

- **`--ignore RULE_ID`** drops one rule (`ai-tells.EmDashUsage`) or a whole style
  (`ai-tells`); repeatable.
- **`--format json`** prints `{"version", "score", "findings", "engines"}`. Each finding:

  ```json
  {"rule_id": "SlopCheck.Sycophancy", "engine": "vale", "severity": "error",
   "line": 1, "col": 1, "span": [0, 14], "match": "Great question",
   "message": "Flattery: 'Great question'. Delete it and start with the content.",
   "suggestion": ""}
  ```

  `span` is `[start, end)` in characters of the input as read: a leading BOM is dropped
  and line endings are normalised to `\n` first. `rule_id` is Vale's check name
  (`ai-tells.*`, `ai-tells-commits.*`, `SlopCheck.*`) or `aiwru.<type>.<rule>`. `suggestion`
  is a replacement where a rule has one (`utilize` → `use`) or aiw-ru's hint; often empty.
  `engines` reports each engine's status (`ok`, `skipped` with a reason, `off`,
  `unavailable`) and version or score.
- **Text format:** `path:line:col rule_id message → suggestion`, then a `score:` line.

**Score** (0 to 100, higher means more slop): for English, weighted findings per 100 words
(error 3, warning 2, suggestion 1; 10 points per 100 words is 100). For Russian, aiw-ru's own
score, or that density when our SlopCheck findings make it higher. Mixed text: the two,
weighted by word count. The score is a summary; the exit code depends on findings only.

### Exit codes

| code | meaning |
|---|---|
| 0 | no findings (an advisory Laya score never counts) |
| 1 | findings |
| 2 | usage error, unreadable input, or a needed engine is missing or failed; the message names the engine and its install command |

aiw-ru is only needed when the text has Russian paragraphs (or `--lang ru`); Vale is always
needed, since the SlopCheck pack runs through it.

### Treating findings

Rewrite the sentence: say the fact, give the number, link the evidence. A finding that is
right for this text (quoting someone, a rule about em dashes in a typography guide) may stay;
say why in the PR or the message. Do not reword a sentence only to get it past a rule.

## Rule packs

Everything lives in [`slop_cli/vale/`](slop_cli/vale/) and ships inside the package, so an
installed `slop-check` works offline and never runs `vale sync`:

- `vale.ini` is the config slop-check passes to Vale (`--no-global`, so your own Vale setup does
  not leak in). It turns off the vendored rules that fired mostly on ordinary engineering
  prose in a trial over 75 real PR bodies and 80 human-written READMEs; each one has its
  reason next to it. Summary: punctuation that technical text uses on purpose (semicolons,
  colons, `--`), sentence shapes that carry facts (statements of absence, step lists, counts),
  and engineering idioms the metaphor rules misread (a process dies, a rule fires, a PR lands).
  `ai-tells.SycophancyMarkers`, `UniversalSubject`/`UniversalObject` and `FormalRegister` are
  replaced by narrower SlopCheck rules.
- `styles/ai-tells/`, `styles/ai-tells-commits/`: tbhb/vale-ai-tells v1.37.0, unmodified, MIT
  (`styles/LICENSE.vale-ai-tells`). Tune them in `vale.ini`, never in their files.
- `styles/SlopCheck/`: our pack, one rule per file, English and Russian. It covers what the
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

### Adding a rule

1. Find the phrase's family in `slop_cli/vale/styles/SlopCheck/` and add a token there, or
   create `NewRule.yml` with a comment saying what it catches and why. Use
   [`existence`](https://vale.sh/docs/checks/existence) for phrases and
   [`substitution`](https://vale.sh/docs/checks/substitution) with `action: {name: replace}`
   for a safe swap. Russian rule names start with `Ru`.
2. Add a line `NewRule<TAB>text it must flag` to `tests/fixtures/rule_samples.tsv`; the
   integration test fails for a rule file without a sample.
3. Before adding a Russian phrase, check whether aiw-ru already reports it
   (`aiw-ru scan --json`), so the two engines do not report the same span twice.
4. Run `slop-check` on `tests/fixtures/clean_en.md` and `clean_ru.md`: they must stay clean.

## Laya (optional)

`--laya` adds an advisory score from [task-cli](https://git.hyperide.ai/ultrabricks/task-cli)'s
Laya classifier. slop-check only talks to a server that is already running, through
`$XDG_STATE_HOME/task-cli/classify/laya-*.sock` (default `~/.local/state/...`), and only when
that directory is private and yours and no other user can write to a directory above it
(the text you check goes to that socket). It never starts the server and never loads a model.
It asks one yes/no question, "Is this text AI-generated slop: buzzwords, stock phrases,
hedging, flattery or vague claims with no concrete facts?", and reports P(yes): `laya:
p(slop)=0.32 (advisory)`, plus a `laya.SlopProbability` suggestion at 0.3 and above. In a
probe on 2026-10-16 obvious English slop scored 0.32, Russian slop 0.60, plain technical
English 0.0007 and plain Russian 0.09. It never changes the exit code. No socket: `laya:
unavailable`, and the check carries on.

## Development

Rules for agents working here: [AGENTS.md](AGENTS.md). Tests: unit tests drive the CLI against
fake engines (`tests/fakes/`) and a fake Laya socket; `tests/test_integration.py` runs the real
Vale and aiw-ru and is skipped when either is missing. CI installs both and sets
`SLOP_CHECK_REQUIRE_ENGINES=1`, which turns that skip into a failure.

```sh
uv run --no-project --with 'pytest>=8,<9' --with 'aiw-ru>=2.5,<3' pytest tests -q
```

## License

MIT. The vendored vale-ai-tells styles are MIT, © 2025-2026 Tony Burns
(`slop_cli/vale/styles/LICENSE.vale-ai-tells`).
