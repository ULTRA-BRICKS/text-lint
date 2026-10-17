# text-lint: one library of text checks for every tool and every repo

- Status: approved with rulings (tg#22710, section 12); tg-cli tasks on hold
- Date: 2026-10-17
- Ticket: [ultrabricks/slop-cli#4](https://git.hyperide.ai/ultrabricks/slop-cli/issues/4)
- Owner request: tg#22605; owner rulings: tg#22710 (section 12)
- Implementation plan: [docs/plans/2026-10-17-text-lint-plan.md](../plans/2026-10-17-text-lint-plan.md)

## 1. Intent

The owner asked for three things in tg#22605:

1. Move the text checks out of tg-cli into a project called **text-lint**, and grow it into the
   largest collection of text checks and tools we have. tg-cli, the voice path ("call-cli") and
   other tools use it.
2. Interface text in any code goes through it.
3. rig enforces it through git hooks and CI.

What this spec assumes on top of the request (the owner can correct any of these):

- text-lint is the renamed slop-cli repo, because slop-cli already holds the AI-tell engines, its
  rule packs and its tests; starting a second repo would split the checks again.
- "Interface text" means every string a person reads: UI labels in apps, CLI output and `--help`
  text, locale catalogs, Markdown docs, commit messages, PR titles and bodies, Telegram messages
  and text spoken by TTS.
- Existing repos must keep working: enforcement checks only the lines a change touches, and it
  starts in a warn-only phase.
- Everything runs offline on the workstation and in CI, with no network call. Laya is mandatory
  and local: the profiles that use it (4.5) ask the task-cli classify server on this machine, which
  holds one copy of the model in RAM (about 2 GB), and nothing goes to the network (owner, tg#22710).

Success means: one implementation of each check, every consumer calling it, every repo running it
on changed lines in a pre-commit hook, a commit-msg hook and a CI job, and a rule contributed once
reaching all of them.

## 2. Inventory of text checks today

Paths below are absolute under `/home/ultra`. tg-cli line numbers are from `~/dev/tg-cli` at
`8ab6a11`, the commit the deployed clone `~/.local/share/tg-cli` also runs. `~/xp/tg-cli` is an
older clone and is not used here.

### 2.1 tg-cli send gates

tg-cli is Bun/TypeScript; the `tg` entrypoint is one script of about 2,870 lines. Every gate is an
inline `if` that calls a pure function and exits with a code; there is no shared check interface.
None of the gates spawns a process, and none calls slop-check or Vale today.

| Gate | Implementation | Wired at | Kind | Override today | Tests | Goes to |
|---|---|---|---|---|---|---|
| Dictionary (tg-cli#302) | `features/cli/dict-gate.ts`: `loadDictionary`:221, `checkDictionary`:326, `resolveDictionaryPath`:376 | `tg`:1580-1637 (title, subagent, caption/body, table rows) | generic | none by design; a missing default file turns it off, a malformed one refuses every send | `tests/dict-gate.test.ts`, `tests/dict-gate-cli.test.ts` | **text-lint** |
| Stray CJK | `features/cli/cjk-guard.ts`: `findStrayCjk`:98 | `tg`:1527-1533 | generic | `--no-feature cjk-guard` | `tests/cjk-guard*.test.ts` | **text-lint** |
| Mixed-script garbage word | `features/cli/mixed-script-guard.ts`: `findMixedScriptTokens`:142 | `tg`:1537-1541 | generic | shares `cjk-guard` | `tests/mixed-script-guard*.test.ts` | **text-lint** |
| Escalation format (`--tag decision`) | `features/cli/escalation-format.ts`: `validateEscalationFormat`:299 | `features/cli/args.ts` `TAG_GATES`:297, dispatched :656-672; `tg`:1324 `enforceEscalationGate` (defined :144-157); table recheck `tg`:1653-1656 | generic | `ESCALATION_GATE_ENFORCE=0` downgrades to a warning | `tests/escalation-format.test.ts`, `tests/escalation-gate-args.test.ts` | **text-lint** |
| Intent (decision / problem / report, #706) | `features/cli/intent-gate.ts`: `classifyIntent`:342, `validateProblemFormat`:266, `validateReportFormat`:298 | `tg`:1708-1729, only for agent-authored sends | classifier generic; the obligations store (`features/cli/obligations.ts`) is Telegram | `ESCALATION_GATE_ENFORCE=0`, `--automated` | `tests/intent-gate*.test.ts` | classifier and validators to **text-lint**; obligations stay |
| Laya call | `features/cli/laya-socket.ts`: `layaAskProbability`:86 (1200 ms timeout, 2000 chars) | inside the intent gate | generic client | the `TG_LAYA` off switch (removed by this design, 4.3) | `tests/laya-socket.test.ts` | **text-lint** (one client instead of two) |
| Long unstructured message (advisory) | `features/cli/message-structure.ts`: `shouldWarnLongUnstructuredPlainMessage`:169 | `tg`:1505 | generic | advisory only | `tests/message-structure.test.ts` | **text-lint** |
| Screenshot mention | `features/cli/screenshot-guard.ts`: `mentionsScreenshot`:23 | `tg`:1559-1578, skipped when a photo is attached | depends on the attachments | `--no-feature screenshot-guard` | `tests/screenshot-guard*.test.ts` | stays in tg-cli |
| Unattached file mention (#648) | `features/cli/file-mention-gate.ts`: `unattachedFileMentions`:101 | `tg`:1667-1707 | stats the file system, depends on the attachments | `--no-feature file-mention-guard` | `tests/file-mention-gate*.test.ts` | stays in tg-cli |
| Never-attach denylist | `features/auto-attach/denylist.ts`: `isNeverAttach`:55 | `features/cli/args.ts`:607 | file names, not text | `--no-feature attach-denylist` | `tests/denylist.test.ts` | stays in tg-cli |
| Flood cap (#208) | `features/auto-attach/transmitter.ts`: `floodCapViolation`:105; limit 6 × 4096 (`types.ts`:46,52) | `transmitter.ts`:245 | Telegram | `--no-feature flood-cap` | `tests/transmitter.test.ts` | stays in tg-cli |
| Rich-HTML limits, caption 1024, 4096 split | `features/render/rich.ts` `validateRichHtml`; `split.ts` | `transmitter.ts`:174 | Telegram | none | `tests/rich.test.ts`, `tests/split.test.ts` | stays in tg-cli |
| Answer gate, `--title` ref refusals, `--tag` vocabulary | `args.ts` `answerGate`:239, :433, :446; `features/render/tag.ts` `validateTag`:82 | parse time | CLI arguments | `--terminal-question` (answer gate) | `tests/reply-table-args.test.ts`, `tests/tag.test.ts` | stays in tg-cli |
| pre-send text and photo hooks | `features/hooks/run-text-hooks.ts` `gateText`:62; `run-photo-hooks.ts` `gatePhotos`:377 | `tg`:2551-2570, 2334-2341 | extension seam | `--no-feature hooks`, `AGENTS_HOOKS=0` | `tests/run-text-hooks.test.ts` | stays in tg-cli |

The voice path has no checks. `tg call --say`, `tg user send` and duplex calls are dispatched at
`tg`:654-657, before every gate above. The only checks are "not empty" and "at most 1000
characters" (`features/telegram_client/tts.py`, `features/telegram_client/account.py`:252).
Duplex calls speak the agent's `responseText` (`features/telegram_client/duplex.py`:211-217) with
no filtering.

### 2.2 slop-cli (becomes text-lint)

`slop-check` 0.1.0, `~/dev/slop-cli`, merged at `9a678d8`:

- Vale 3.24.0 with vendored tbhb/vale-ai-tells v1.37.0: `ai-tells` (137 rules, about 40 turned off
  in `slop_cli/vale/vale.ini`, each with its reason) and `ai-tells-commits` (15 rules, used for PR
  bodies).
- Our `SlopCheck` Vale style, 16 rules: `DoneEmoji`, `Hedging`, `Intensifiers`, `OfferMore`,
  `RuDannyi`, `RuListIntro`, `RuOfferMore`, `RuStatusReport`, `RuStoitOtmetit`, `RuSycophancy`,
  `RuVCelyah`, `RuVazhnoPonimat`, `StatusReport`, `Sycophancy`, `UnprovenTestClaim`, `Utilize`.
  Each has a sample in `tests/fixtures/rule_samples.tsv`.
- aiw-ru 2.5 for Russian (`aiw-ru scan --json --context …`), on Russian paragraphs only.
- A Laya slop score through the task-cli classify server (`slop_cli/laya.py`), asked only with
  `--laya` today.
- The wrapper (`slop_cli/check.py`) routes paragraphs by Cyrillic share, drops findings inside code
  and URLs, merges findings, scores 0-100, and exits 0 / 1 / 2.
- Contexts (`slop_cli/engines.py`:22-28): none, `issue`, `pr`, `tg`, `docs`.
- Open issue: slop-cli#3 (indented code blocks, invalid YAML front matter); it carries over.

Measured on the workstation today: one Russian line through `slop-check --context tg` takes
1.17-1.25 s with a peak RSS of 65-71 MB. aiw-ru alone takes 0.64-0.81 s, of which 0.47 s is
importing its lexicon; Vale alone takes 0.08 s.

### 2.3 The owner's dictionary

`~/.claude/DICT.json`: `{version: 1, description, rules[]}`, 13 rules, each
`{id, pattern, flags, replacement, why, only_if_cyrillic?}`. It is the machine-readable twin of the
Dictionary section in `~/.claude/CLAUDE.md`. Two implementations read it:

- tg-cli `features/cli/dict-gate.ts` (send time; fails closed on a malformed file);
- agent-tools `agent-hooks/chat-dictionary-gate/chat_dict_gate.py`, a Python port used as a Claude
  Code Stop hook (fails open; timeout 3000 ms; parity test in
  `tests/test_chat_dictionary_gate.py`:877-890).

The data stays where it is (it is the owner's, next to CLAUDE.md). The matcher moves to text-lint
and both copies are deleted.

### 2.4 Hooks and CI in agent-tools, rig-cli and `~/.claude`

- `agent-tools/agent-hooks/decision-request-format/decision_request_format.py`: pre-bash hook on
  `tg --tag decision|problem`; grades Context / Options / Recommendation. It checks the same format
  as tg-cli's escalation and intent gates. Goes to text-lint (tg-cli then enforces it once).
- `agent-tools/agent-hooks/chat-dictionary-gate/`: see 2.3.
- `agent-tools/ci/pr-title-lint/workflow.yml`: Conventional Commits on the PR title. It is not
  prose linting and stays.
- `agent-tools/skills/universal/slop-check/SKILL.md` (agent-tools#929): advice only; nothing runs
  slop-check automatically.
- `agent-tools/skills/by-stack/frontend/ts/anti-slop/`: Oxlint rules for code, not prose. Stays.
- The git hook dispatcher (`agent-tools/git-hooks/global-dispatcher/`, installed by rig to
  `~/.config/git/`) runs only `pre-commit/10-secret-scan` and `pre-push/10-protect-main`; the
  `commit-msg` event has no fragment. No hook or CI job lints commit messages, PR bodies, docs or
  UI strings.

### 2.5 Other tools

- **review-cli** (`~/work/review-cli`): presets `light` (default, `reviewlib/config.py`:686),
  `default`, `heavy` (:677-679) are LLM reviewer boards. No preset lints prose and the package calls
  no linter.
- **task-cli** (`~/dev/task-cli/tasklib/policy.py`): ticket gates on structure (acceptance criteria,
  motivation, user impact and its quality in `tasklib/quality.py`, links, junk titles, a screenshot
  mention at :1346). No prose or dictionary check.
- **call-cli** does not exist as a repo. The voice path lives in tg-cli (2.1). A future
  `call-client` is listed in `~/dev/aisis/docs/architecture/repo-layout.md`:19.
- **qa-cli**, **dev-cli**: no text checks. qa-cli#42 is about rig's install marker and is unrelated.
- **stt-cli** corrects transcripts with a glossary; that is correction, not linting, and stays.
- **HyperSummaryBot**, **HyperCalendarBot** send LLM text to Telegram and TTS with HTML sanitizing
  only. They run on servers, so they are not v1 consumers (section 5.7).

### 2.6 What moves and what stays

Moves into text-lint: the dictionary matcher, stray-CJK, mixed-script, the escalation / problem /
report format checks, the intent classifier, the long-message advisory, the Laya client, the
decision-request-format hook's grading, and all of slop-check.

The decision, problem and report checks keep running on every `tg` send exactly as today. Only
their code moves: tg-cli calls text-lint for them instead of its own copy, and PR, issue and review
texts can then use the same rules.

Stays in the consumer, because it depends on attachments, Telegram limits or CLI arguments:
flood cap, caption and split limits, rich-HTML limits, never-attach denylist, screenshot and
file-mention gates, answer gate, `--title` refusals, `--tag` vocabulary, the obligations store, the
hook seam, and task-cli's ticket-structure gates.

## 3. Approaches considered

**A. One Python implementation; consumers call the CLI with a JSON contract (chosen).** text-lint
keeps slop-cli's Python core, takes over the TypeScript and Python gates, and every consumer runs
`text-lint check --input-json - --format json` and reads a versioned JSON report (`--input-json`
sets only the input; output stays text unless `--format json` is given).

**B. Rules as shared data, two engines (TypeScript in tg-cli, Python elsewhere).** Keeps tg-cli's
sub-millisecond gates, but every rule kind is written twice and Vale and aiw-ru still cannot run
inside Bun. This is the duplication we are removing.

**C. A resident text-lint server on a Unix socket.** Would hide aiw-ru's 0.47 s import, but adds a
second long-lived process on a memory-constrained machine, plus lifecycle code like the Laya
server's. Rejected for v1; the latency budget in 5.1 holds without it.

Why a subprocess contract and not a native Python package import for Python consumers:
agent-tools hooks run the system `python3`, review-cli and task-cli run in their own pipx/uv
virtualenvs, and text-lint needs Python 3.12 or newer plus aiw-ru. Importing it would put those
requirements into every consumer's environment and tie them to one installed version. A process
boundary with a JSON schema costs about one process start and lets each side upgrade separately.
TypeScript consumers get the same contract; there is no TypeScript port.

## 4. text-lint itself

### 4.1 Repo rename and migration of the name

1. Rename the forge repo `ultrabricks/slop-cli` to `ultrabricks/text-lint` in Forgejo's settings.
   Forgejo keeps a redirect from the old name (inherited from Gitea's repo redirects), so old clone
   URLs, issue links and PR links keep working.
2. Python package `slop_cli` becomes `text_lint`; the distribution `hyper-slop-check` becomes
   `hyper-text-lint`; the local checkout moves from `~/dev/slop-cli` to `~/dev/text-lint`.
3. Two console scripts during migration: `text-lint` and `slop-check`. `slop-check` is a shim that
   translates its 0.1 arguments (`--context issue|pr|tg|docs` → `--profile issue|pr|tg-bot|prose`,
   any finding exits 1 as before through `--fail-on suggestion`), prints one deprecation line to
   stderr, and runs `text-lint check`. `--ignore SlopCheck.X` maps to `TextLint.X`.
   `slop-check install-skill`, which `install.sh` calls, runs `text-lint install-skill`.
4. The shim is removed in the first release after both hold: 30 days have passed since the rename,
   and a search of `~/dev`, `~/xp`, `~/work` and agent-tools finds no `slop-check` caller.
5. Updated with the rename: remotes in clones, `tools.items` in `~/.config/rig/config.yaml`
   (`slop-check` → `text-lint`, `repo: ~/dev/text-lint`, `brew: [vale]`), this repo's `rig.yaml`
   `task.forgejo.repo`, the skill (agent-tools `skills/universal/slop-check` → `text-lint`), ADR 0001
   (its claim that DICT.json is missing on the Linux workstation is no longer true).

### 4.2 Package layout

```
text_lint/
  __init__.py            __version__ (single source)
  cli.py                 subcommands; argument parsing only
  check.py               one run: units → engines → suppressions → report
  report.py              Finding, report@1 builder, verdict, exit code
  inputs.py              plain / Markdown / HTML / input@1 JSON → units
  profiles.py            profile loader
  profiles/*.yaml        prose, tg-bot, as-user, chat, spoken, ui-strings, issue, pr, commit
  config.py              .text-lint.yaml, enforce mode from rig.yaml
  suppress.py            inline and config suppressions
  text.py                masking of code and URLs, paragraphs, HTML stripping
  engines/
    vale.py  aiwru.py  laya.py  native.py
  native/                code-backed rule kinds (one module per kind)
    pattern.py  dictionary.py  script.py  formats.py  intent.py  structure.py  spoken.py  as_user.py
  packs/
    vale/vale.ini
    vale/styles/{ai-tells, ai-tells-commits, TextLint}/   (SlopCheck renamed TextLint)
    native/{script, formats, structure, spoken, as-user}/*.yml      one rule per file
  extract/
    diff.py              changed lines from git
    classify.py          path → prose | code | locale | skip
    code.py              ast-grep-py extraction
    rules/{tsx,typescript,javascript,python,swift,kotlin}.yml
    locale.py            JSON, YAML, .po, TOML, WebExtension messages.json
  selftest.py
  schema/                report-v1.json, input-v1.json, native-rule-v1.json (installed with the package)
```

Every runtime asset (profiles, Vale styles and `vale.ini`, native packs, extraction rules, schemas)
lives inside the `text_lint` package and is listed in `pyproject.toml` package data; CI installs
the built wheel into a fresh environment outside the checkout and runs `text-lint selftest` there,
so a missing asset fails CI.

### 4.3 CLI surface

```
text-lint check [PATH|-] [--profile NAME] [--input-json] [--format text|json]
                [--lang auto|en|ru] [--enforce warn|block|repo] [--fail-on error|warning|suggestion]
                [--ignore RULE_ID]... [--dict PATH] [--laya-mode required|ci] [--cleanup editor|none]
text-lint diff (--staged | --worktree | --base REF [--head REF]) [--format text|json]
               [--enforce warn|block|repo]
text-lint extract PATH... [--format text|json]     # show the UI strings a file yields
text-lint rules [--profile NAME] [--format text|json]
text-lint selftest [--rules GLOB]
text-lint install-skill
text-lint --version
```

- `check` reads a file or stdin. `--profile` defaults to `prose`. `--input-json` reads an input@1
  document (4.4) instead of raw text.
- `diff` checks only the lines a change touches (section 6) and is what hooks, CI and review-cli
  call: `--staged` is the index against `HEAD`, `--worktree` the working tree against the index
  (plain `git diff`), `--base REF` the commits in `REF...HEAD`, or `REF...<head>` with `--head`,
  which also makes text-lint read every file from that commit (`git show`) instead of the working
  tree, so CI can lint a PR it never checks out.
- `--ignore` drops a rule or a whole style for this run; it is how a consumer maps its own feature
  flags (tg-cli's `--no-feature cjk-guard`). Repo-wide disabling goes through `.text-lint.yaml`
  with a reason (7.3).
- `--dict` overrides the dictionary path; otherwise `TEXT_LINT_DICT`, then `TG_DICT_PATH` (kept
  for tg-cli's existing users), then `~/.claude/DICT.json`. An explicit path that does not exist is
  a configuration error; a missing default file means no `Dict.*` rules.
- Laya: the profile decides which questions text-lint asks the local Laya server (4.5); there is
  no switch that turns Laya off. When a profile uses Laya and the socket refuses or is absent,
  text-lint runs `task classify-server start` once (5.5), waits for it, and sends the request
  again; when Laya still cannot answer, the run exits 2 naming Laya, never a silent skip. Budget:
  1.5 s per warm request, at most 20 s waiting for a cold start (4.7).
- `--laya-mode ci` is for CI runners, which have no Laya model: the run asks Laya nothing and the
  report (and the CI job summary) says `laya: not run (ci runner)`. It is accepted only when the
  environment has `CI=true`; anywhere else it is a usage error (exit 2). The default is
  `--laya-mode required`.
- `TEXT_LINT_LAYA_SOCKET` and `TEXT_LINT_LAYA_TIMEOUT_MS` choose a non-default socket and timeout
  (tests use them); tg-cli maps its `TG_LAYA_SOCKET` and `TG_LAYA_TIMEOUT_MS` to them.

Exit codes, the same for every subcommand:

| Code | Meaning |
|---|---|
| 0 | verdict `pass` or `warn` |
| 1 | verdict `block` |
| 2 | usage error, unreadable input, invalid configuration (dictionary, `.text-lint.yaml`, profile), or a required engine missing or failed |

Verdict: `block` when enforce mode is `block` and at least one finding is at or above the
profile's `fail_on` severity; `warn` when there are findings but none blocks; `pass` when there are
none. The Laya slop score (`laya.SlopProbability`) is reported and never counts; `Intent.*` and
`AsUser.NotFirstPerson` findings, which come from Laya answers, do. Enforce mode comes from `--enforce`; `--enforce repo`
reads the repo's `rig.yaml` (8.1) and is the default for `diff`; `check` defaults to `block`.

### 4.4 JSON contract

Input (`--input-json`), schema `text_lint/schema/input-v1.json`:

```json
{
  "schema": "text-lint/input@1",
  "fields": [
    {"name": "title", "text": "…", "format": "plain"},
    {"name": "body", "text": "…", "format": "html"}
  ],
  "meta": {"tag": "decision", "agent_authored": true, "automated": false, "send_format": "html",
           "attachments": 0, "table": false}
}
```

`format` is `plain`, `markdown` or `html`. HTML fields are checked with `<pre>` and `<code>`
masked, tags removed and entities decoded (tg-cli's `stripForDictionary` rule); Markdown fields
with code spans, code blocks and URLs masked; plain fields as written. A field may carry
`"gate": false`: its findings are reported but never count toward the verdict (tg-cli uses this for
a generated title it drops instead of refusing, 5.1). `meta` keys are optional; `tag` turns on the
format rules for that tag; `attachments` (count of attached files) and `table` (sent with
`--table`) switch the long-message advisory off, as `shouldWarnLongUnstructuredPlainMessage` does
today (`features/cli/message-structure.ts`:169-172).

Report, schema `text_lint/schema/report-v1.json` (an extension of slop-check 0.1's report):

```json
{
  "schema": "text-lint/report@1",
  "version": "0.4.0",
  "profile": "tg-bot",
  "enforce": "block",
  "verdict": "block",
  "score": 42,
  "findings": [
    {"rule_id": "Dict.skameyka-for-bench", "engine": "native", "severity": "error",
     "field": "body", "path": null, "line": 1, "col": 9, "span": [8, 15],
     "match": "скамейку", "message": "…", "suggestion": "бенч / бенчмарк"}
  ],
  "suppressed": [
    {"rule_id": "Script.MixedScript", "path": "src/i18n/ru.json", "line": 12, "reason": "…"}
  ],
  "engines": {"native": {"status": "ok"}, "vale": {"status": "ok", "version": "3.24.0"},
              "aiw-ru": {"status": "skipped", "reason": "no Russian text"}, "laya": {"status": "ok"}},
  "intent": {"kinds": ["decision"], "action": "follow-up", "reason": "…",
             "follow_ups": [{"kind": "decision", "quote": "…"}], "valid": null,
             "hints": [], "laya_p": null}
}
```

`field` is set for input@1 runs, `path` for `diff` runs, and `key` (the dotted key path, or the
`.po` msgid) for strings from locale files; `line` and `col` are 1-based within the field, or
within the source file for `diff` runs, mapped back through any normalization done by extraction.
`intent` appears only in profiles that run the intent classifier. It carries every
field of tg-cli's `IntentVerdict` (`features/cli/intent-gate.ts`:56-61) that the obligations store
reads: `action` (`pass`, `follow-up` or `block`), `kinds`, `reason`, `follow_ups` (one
`{kind, quote}` per owed item, as `FollowUp` at :47), `valid` (`decision`, `problem` or `null`:
what a reply settles) and `hints`; a `block` also produces an `Intent.*` finding. A
breaking change to either document becomes `@2`, and the release that introduces it still accepts
`--schema 1` for one minor release.

### 4.5 Profiles

A profile is a YAML file in `text_lint/profiles/` that names the engines, the rule packs, the
markup, `fail_on`, and per-engine options. v1 ships these:

| Profile | Used for | Engines | `fail_on` |
|---|---|---|---|
| `prose` | Markdown docs, READMEs, ADRs, skills | native (dict, script) + Vale `ai-tells`, `TextLint` + aiw-ru `docs` | warning |
| `issue` | ticket titles and bodies | as `prose`, aiw-ru `technical`, + Laya required (slop) | warning |
| `pr` | PR title and body | as `prose` plus `ai-tells-commits`, + Laya required (slop; `--laya-mode ci` on CI runners, 8.3) | warning |
| `commit` | commit messages; `--cleanup editor` (what the commit-msg hook passes) drops `#` comment lines and everything below git's scissors line, as git's default cleanup does; without it (CI reading a stored message) the message is checked as committed; trailers are always kept, since `ai-tells-commits.CommitAttribution` reads them | native + Vale `ai-tells-commits`, `TextLint` + aiw-ru `technical` | warning |
| `tg-bot` | messages the bot sends (every plain `tg` send) | native (dict, script, formats, intent, structure) + Vale `.tg` + aiw-ru `telegram` + Laya required (`asks_reader` for intent, slop); emoji allowed: every rule whose only target is emoji, `TextLint.DoneEmoji` included, is off | error |
| `as-user` | text sent as the owner (`tg user send`, `tg call --say`) | native (dict, script, `AsUser.*`) + Vale `.tg` + aiw-ru `chat` + Laya required (slop, `first_person`); emoji and bot formatting are errors | error |
| `chat` | agent replies (Stop hook) | native (dict, script) only | error |
| `spoken` | text spoken as the owner in duplex calls | the `as-user` rules plus `Spoken.Markup` | error |
| `ui-strings` | strings extracted from code and locale files | native (dict, script) + Vale `TextLint` + aiw-ru `chat` | warning |

In `tg-bot`, `as-user` and `spoken`, a missing Vale or aiw-ru is reported as skipped, not exit 2,
and their findings are capped at `warning` during the warn phase, so they show but do not block a
send. After the warn phase (section 9, rollout step 6) the cap is lifted and `fail_on` of `tg-bot`
and `as-user` drops from `error` to `warning` in the same change; lifting the cap alone would leave
a Vale `warning` such as `TextLint.Utilize` below `fail_on: error`, so it would still not block.
Laya is different: these
profiles require it, and an unreachable Laya is exit 2. aiw-ru always runs with `--no-model`, so an
installed aiw-ru model is never loaded.

The consumer picks `tg-bot`, `as-user` or `spoken` from the send path in code (5.1, 5.2); no CLI
flag or option lets a person choose the profile of a send. `commit`, `ui-strings`, `prose`, `chat`
and `diff` runs ask Laya nothing: a hook run checks many small strings within a 3 s budget, and a
model call per run does not fit it.

Laya questions: `slop` (slop-check's existing question), `asks_reader` (tg-cli's intent question)
and `first_person` ("Does this read as a person writing in the first person, not as a bot or
report?"). A `first_person` probability below 0.5 gives `AsUser.NotFirstPerson` (error).

### 4.6 Rule packs and how rules are contributed

Two kinds of rules:

1. **Vale rules** (phrases and patterns in English and Russian) stay Vale YAML under
   `packs/vale/styles/TextLint/`, one rule per file, each with at least one line in
   `text_lint/packs/vale/samples.tsv` (moved from `tests/fixtures/rule_samples.tsv`, so an installed
   wheel carries the positive controls `text-lint selftest` runs) that it must flag. Vendored `ai-tells` and `ai-tells-commits`
   are never edited; they are tuned in `vale.ini` with a reason per disabled rule (ADR 0001).
2. **Native rules** are YAML files in `packs/native/<pack>/`, one rule per file, validated by
   `text_lint/schema/native-rule-v1.json`. Each names a code-backed `kind`, its parameters, and its own
   positive and negative examples:

```yaml
id: Script.StrayCjk
kind: stray-cjk
severity: error
profiles: [prose, issue, pr, commit, tg-bot, as-user, chat, spoken, ui-strings]
message: "Stray CJK character '{match}' inside a word; delete it."
params: {dominant_share: 0.5, dominant_min: 3}
examples:
  flag: ["Проверил 的тесты, всё зелёное"]
  pass: ["日本語のテキストです", "Commit 8ab6a11 fixes the cache"]
```

v1 kinds: `pattern` (regex with DICT.json semantics: `\b` becomes a Unicode word boundary, the `u`
flag is forced, optional `only_if_script: cyrillic` judged over all gating fields together, while a
`"gate": false` field is judged on its own, as tg-cli checks its generated title separately today), `stray-cjk`,
`mixed-script`, `format` (named validators `decision`, `problem`, `report`; `decision` also requires
the sender's explicit `meta.send_format` to be `html`, as tg-cli's `escalationFormatGate` does at
`features/cli/args.ts`:282-285, because a structured body sent without `--format html` shows literal
tags; the per-field `format`, which tg-cli detects from the markup, does not count; `problem`,
`report` and the `intent` kind, with its Laya question, apply only when `meta.agent_authored` or
`meta.automated` is true, as tg-cli runs them today at `tg`:1708-1715, so a person's
`tg --tag report "Backup complete"` still goes through; `decision` applies to every sender), `intent`,
`long-unstructured`, `spoken-markup` (Markdown, HTML, URLs, code and emoji that TTS would read
aloud), `as-user-emoji` (any emoji codepoint → `AsUser.Emoji`, error) and `bot-formatting` (HTML
tags, Markdown headings, emphasis, list markers and code spans, and tag badges →
`AsUser.BotFormatting`, error).

Each kind also declares what it reads and where it runs:

- `pattern` reads the normalized text (HTML stripped, code masked, as above) and runs on every
  field, which keeps tg-cli's rule that quoting a banned word inside `<code>` is allowed.
- `stray-cjk` and `mixed-script` read the raw field text and run on every field, as tg-cli's guards
  do today: garbled characters are a defect inside code too (`tests/cjk-guard.test.ts`:87 requires
  catching them there). HTML tags are ASCII and cannot trigger them.
- `spoken-markup`, `as-user-emoji` and `bot-formatting` read the raw field text, because the
  markup, code spans and URLs they report are exactly what normalization removes; they run on
  every field.
- Message kinds (`format`, `intent`, `long-unstructured`) read the raw field text, because tg-cli's
  validators look for the HTML structure itself (`<h3>` sections, `<hr>`, lists; see
  `validateProblemFormat`, `features/cli/intent-gate.ts`:266-281), and run only on the message
  body: the field named `body`, or `caption` when there is no `body`. A title, subagent label or
  generated title never triggers them, as today, where tg-cli classifies only the caption
  (`tg`:1708). When the input is not input@1 (a file or stdin, which yields one unit without a
  field name), that single unit is the message unit.

DICT.json is loaded as `pattern` rules with ids `Dict.<id>`; `only_if_cyrillic: true` becomes
`only_if_script: cyrillic`. Its file format stays version 1.

Rule id namespaces: `Dict.*`, `Script.*`, `Format.*`, `Intent.*`, `Structure.*`, `Spoken.*`,
`AsUser.*` (`AsUser.Emoji`, `AsUser.BotFormatting`, `AsUser.NotFirstPerson`),
`TextLint.*` (our Vale rules and `TextLint.SuppressionWithoutReason`), `ai-tells.*`,
`ai-tells-commits.*`, `aiw-ru.*`, `laya.SlopProbability`.

Contributing a rule is a data change plus its test: one file, at least one `flag` example (or a
`samples.tsv` line for Vale rules), at least one `pass` example for native rules, a CHANGELOG
line, and a minor version bump. A new `kind` is a code change with its own unit tests. The test
suite fails when any rule lacks a positive example (10.1).

### 4.7 Versioning and offline operation

- SemVer from `text_lint.__version__`. A new or newly enabled rule, or a new profile, is a minor
  bump; tuning that only removes findings is a patch. The report and input schemas carry their own
  `@N` versions (4.4).
- The workstation runs the version rig installs from `main` (`tools.items`). The CI slot pins a
  git tag and a Vale release with its SHA-256. CI is the authority when the two disagree.
- At run time text-lint makes no network call: Vale styles are vendored (no `vale sync`), aiw-ru
  runs with `--no-model`, ast-grep grammars ship inside the `ast-grep-py` wheel, and Laya is a
  local Unix socket to the task-cli classify server. Installation needs the network; checking
  does not.
- Laya budget: a warm request times out after 1.5 s (a measured warm call takes about 0.2 s,
  task-cli CHANGELOG:41-44). A cold start, which `task classify-server start` performs, may take up
  to 20 s (measured about 3 s: torch import 0.5 s, model load 2.8 s). After the server is up,
  text-lint sends one request; any failure is exit 2. The server exits after 15 min idle
  (`IDLE_SECONDS=900`), so the first send after an idle period pays the cold start once.
- New dependencies: `ast-grep-py` (0.51.0 verified locally: extracts TSX, Python, Swift, Kotlin,
  JSON and YAML strings with line numbers) and `PyYAML` for profiles, packs and `.text-lint.yaml`.

## 5. Consumers

Every consumer runs the `text-lint` binary on PATH, sends input@1 on stdin, and reads report@1.
Each consumer has one contract test that runs the real binary on a known sample and expects exit 1
with the right rule id, so a broken wire is caught.

### 5.1 tg-cli

- One `text-lint check --profile tg-bot --input-json - --format json` call per bot send (every plain
  `tg` send; `tg user send` and the voice path use `as-user`, 5.2), placed
  where the dictionary gate runs now (`tg`:1580), after the caption is assembled and before the
  `--dry-run` cut-off, so `--dry-run` runs it. Fields: `title`, `subagent`, `body` (or `caption`),
  `table_heading`, `table_rows` and `harness_title`, each with the format tg-cli detects today.
  `meta.tag`, `meta.agent_authored` and `meta.automated` come from the existing argument parsing;
  `meta.send_format` is the explicit `--format` value (`null` when none was given); `meta.attachments`
  and `meta.table` come from the attachment plan and `--table`.
- `harness_title` is the generated title candidate (`safeHarnessTitle`, `tg`:1385-1390). A finding
  on it never refuses the send: tg-cli drops the candidate, as it does today at `tg`:1546 and :1633,
  and the default title and session label (`tg`:1645) fall back as now. tg-cli sends it with
  `"gate": false` (4.4), so its findings are reported but do not count toward the verdict.
- tg-cli maps the result: verdict `block` → print the findings, exit 1 (as today); `intent.action`
  `follow-up` → record one obligation per `intent.follow_ups` item as today; `intent.valid` →
  settle an owed obligation as today; `ESCALATION_GATE_ENFORCE=0` → tg-cli treats
  `Format.*` and `Intent.*` findings as warnings; `--no-feature cjk-guard` →
  `--ignore Script.StrayCjk --ignore Script.MixedScript`.
- Failure handling keeps today's dictionary semantics: `text-lint` not on PATH → one stderr warning
  and the send goes ahead (as a missing DICT.json does today); exit 2 → refuse with the message
  (as a malformed DICT.json does today). In this profile a missing Vale or aiw-ru is reported as
  skipped, so exit 2 comes from a configuration error or from Laya being unreachable after the
  on-demand start (4.3); the refusal names the cause.
- Removed from tg-cli after the parity tests pass (10.3): `features/cli/dict-gate.ts`,
  `cjk-guard.ts`, `mixed-script-guard.ts`, `escalation-format.ts`, the classifier and validators in
  `intent-gate.ts`, `message-structure.ts`, `laya-socket.ts`, the escalation entry in `TAG_GATES`,
  and their tests.
- Latency budget: at most 1.5 s at p95 added per send on the workstation for a 4096-character mixed
  Russian and English message with Laya warm (measured baseline: 1.17-1.25 s for slop-check on one
  Russian line; a warm Laya call about 0.2 s), plus a one-off cold start of at most 20 s on the
  first send after 15 min idle (4.7). A send already spends about 1.3 s on `ps`, `lsof` and session discovery
  (tg-cli CHANGELOG.md:639-640). The budget is pinned by a benchmark test in text-lint (10.5).

### 5.2 The voice path ("call-cli")

There is no call-cli repo; the voice path is in tg-cli and follows the same contract.

Text spoken or sent as the owner uses the identity profiles; the send path picks them in code:

- `tg call --say TEXT`: `--profile as-user` before synthesis; a block refuses the call with the
  findings.
- `tg user send`: `--profile as-user` without `meta.tag`.
- Duplex calls (`features/telegram_client/conversation.py`): each agent reply is checked with
  `--profile spoken` (the `as-user` rules plus `Spoken.Markup`) before `tts.synthesize`; findings,
  an exit 2 and a timeout are logged and never stop live speech.
- Budget for `spoken`: the native rules take at most 250 ms at p95 for a 1000-character reply;
  the Laya request adds about 0.2 s warm, within its 1.5 s timeout. The duplex call waits at most
  2 s for text-lint.
- A future call-client (aisis `repo-layout.md`:19) uses the `spoken` profile the same way.

### 5.3 agent-tools

- `chat-dictionary-gate` Stop hook: calls `text-lint check --profile chat --input-json - --format json` and
  blocks on exit 1; keeps fail-open on exit 2 or a timeout (its 3000 ms budget). The Python matcher
  in `chat_dict_gate.py` is deleted.
- `decision-request-format` pre-bash hook: deleted, because tg-cli runs the same `Format.*` rules
  on the same message and refuses with the same advice.
- Skill: `skills/universal/slop-check` becomes `skills/universal/text-lint` (profiles, suppression
  syntax, exit codes); the anti-slop skill's pointer is updated.
- New hook fragments and CI slot, provisioned by rig (section 8).

### 5.4 review-cli

`review diff` runs `text-lint diff --format json --enforce warn` over the same diff the reviewers
see: `--staged` when the review uses `--staged` (review-cli's `git diff --cached`,
`reviewlib/cli.py`:268), `--worktree` for the default working-tree review. A diff piped on stdin
(review-cli tracks it as `diff_from_stdin`, `reviewlib/cli.py`:7006) matches no git source, so the
block is skipped for it. It adds a "text-lint findings" block to the reviewer prompt, telling
reviewers these are already reported and not to repeat them. No new preset; no blocking. A missing
binary skips the block.

### 5.5 task-cli

A new policy gate `prose`: `text-lint check --profile issue --input-json - --format json` on the title and body
at `task create` and `task change`. It starts as a warning (like `todo-shaped`), skippable with
`--skip-prose REASON`, and becomes blocking in rollout step 6.

task-cli also gets `task classify-server start`, the command text-lint runs to start Laya on
demand. It spawns the server through the existing `tasklib.classify_server._spawn` path, waits
until the socket accepts a connection (at most 20 s), prints the socket path and exits 0; it exits
1 when the server cannot start. It is idempotent: with the server running it exits 0 without a
second spawn, and the server's singleton flock lets only one of several concurrent starts spawn
(`tasklib/classify_server.py`:10-27). text-lint runs in another virtualenv and must not import
tasklib, so a command is the boundary. No systemd unit is needed, because the server exits by
itself after 15 min idle.

### 5.6 Not consumers

qa-cli, dev-cli and stt-cli have no text checks to replace and emit no user-facing prose that
text-lint should gate.

### 5.7 Later, outside this spec

HyperSummaryBot and HyperCalendarBot produce Russian text for Telegram and TTS, but they run on
servers where Vale and aiw-ru are not installed. They can adopt the same contract with the `chat`
or `spoken` profile once text-lint is packaged for their deployment; no ticket is filed now.

## 6. UI strings in code

### 6.1 Scope: changed lines only

`text-lint diff` reads `git diff -U0 --no-color -M --diff-filter=AMR` (`--cached` for `--staged`,
no extra argument for `--worktree`, `<base>...HEAD` or `<base>...<head>` for `--base`), keeps the added and modified line numbers per file (a renamed file
contributes only its edited lines), and classifies each file by path. File contents and
`.text-lint.yaml` are both read from the snapshot being checked: the index for `--staged`
(`git show :<path>`), the working tree for `--worktree`, `HEAD` or the `--head` commit for
`--base`, so an unstaged edit never changes what a commit is judged by. With `--staged` it also
appends one line per finding to `~/.cache/text-lint/hook.jsonl` (rule id, repo, path, line,
verdict; never the text) for the rollout triage in section 9.

| Class | Paths | Profile |
|---|---|---|
| prose | `*.md`, `*.mdx`, `*.rst`, `*.txt` | `prose` |
| code | `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.mjs`, `*.py`, `*.swift`, `*.kt` | `ui-strings` |
| locale | `*.po`, `*.pot`, `**/_locales/*/messages.json`, `**/{locales,i18n,lang}/**/*.{json,yaml,yml,toml}` | `ui-strings` |
| skip | everything else, and the default excludes below | none |

Default excludes: `**/node_modules/**`, `**/vendor/**`, `**/third_party/**`, `**/tests/fixtures/**`,
`**/testdata/**`, `**/__snapshots__/**`, `*.min.js`, and vendored Vale styles. A repo adds its own
in `.text-lint.yaml` (`exclude:`).

For prose files the whole paragraph around a changed line is checked (aiw-ru needs the context),
and only findings on changed lines are reported. For code and locale files a string is checked when
its range touches a changed line.

Units are grouped by profile, and each group is batched into one Vale call and one aiw-ru call, with
an offset map back to file and line. A commit that touches docs and UI strings therefore costs at
most two processes of each engine, however many strings it touches, and each group keeps its own
Vale styles and aiw-ru context.

### 6.2 Extraction

Code extraction uses `ast-grep-py` with per-language rule files in `text_lint/extract/rules/`:

- TSX / JSX: JSX text children; string and template literals in the attributes `title`,
  `placeholder`, `alt`, `aria-label`, `aria-description`, `label`, `tooltip`, `helperText`; the
  first literal argument of `alert`, `confirm`, `toast*`, `notify`, `console.log|warn|error`,
  `process.stdout.write`, `process.stderr.write`.
- TypeScript / JavaScript (non-JSX): the same calls.
- Python: arguments of `print`, `sys.stdout.write`, `sys.stderr.write`, `click.echo`, `typer.echo`,
  `gettext`, `_`, `ngettext`; the `help=`, `description=` and `epilog=` keyword arguments.
- Swift: `Text("…")`, `Button("…")`, `Label("…")`, `NSLocalizedString("…")`.
- Kotlin: `Text("…")`, `Toast.makeText(_, "…")`, `getString`-style resources are in XML and out of
  scope.

Template literals and f-strings keep their static parts; each interpolation becomes `{}`. Each
extracted string keeps a map from every character of its checked text back to its line and column
in the source file, so a finding after an interpolation or inside a multi-line string points at the
right place. Strings
without a space and with only identifier characters (`user_id`, `camelCase`, `--flag`) and strings
of fewer than two words are skipped. A repo adds call names per language in `.text-lint.yaml`
(`ui.calls:`).

Locale files use the same library where it gives line numbers: JSON and YAML leaf string values
through ast-grep-py; WebExtension `messages.json` takes only `message` values; `.po` files go
through a small block parser (`msgid` in the source catalog, `msgstr` in translations); Hugo TOML
`key = "value"` lines through a line scanner. GNU `xgettext` and `pybabel` are installed on the
workstation but extract only gettext calls, without JSX or the CLI calls above, so they are not
used.

## 7. Suppressions

### 7.1 Inline, with a reason

In any comment syntax (`//`, `#`, `/* */`, `<!-- -->`, `{/* */}`):

```
text-lint-disable-line RULE_ID[,RULE_ID] -- reason
text-lint-disable-next-line RULE_ID[,RULE_ID] -- reason
<!-- text-lint-disable RULE_ID -- reason -->  …  <!-- text-lint-enable -->
```

The block form is for docs that quote the phrases they discuss (ADRs, skills). The reason is the
text after `--` and must be at least 10 characters. A suppression without one does not apply, and
the run reports `TextLint.SuppressionWithoutReason` (error) on its line; that rule cannot itself be
suppressed. A rule id must be a full id or a style name (`ai-tells`).

### 7.2 Inputs without comments

Telegram messages, chat replies and spoken text have no suppression: these are the owner-facing
channels, and the dictionary there has no bypass by design. Locale files, which cannot hold
comments, use `.text-lint.yaml`.

### 7.3 `.text-lint.yaml`

```yaml
version: 1
exclude: ["docs/samples/**"]
disable:
  - rule: ai-tells.EmDashUsage
    reason: "Russian docs use the em dash as punctuation"
suppress:
  - path: src/i18n/ru.json
    key: errors.network          # or: line_match: "Wi-Fi"
    rule: Script.MixedScript
    reason: "Wi-Fi is the product name"
ui:
  calls:
    tsx: [toast.show]
```

Every `disable` and `suppress` entry needs a reason of at least 10 characters; one without is a
configuration error (exit 2). The file holds no enforce mode (8.1). Every applied suppression is
listed in the report's `suppressed` array and in the CI job summary, so reviewers see them.

## 8. Enforcement by rig

### 8.1 The one switch: `rig.yaml`

A repo's enforce mode is `ci.items.text-lint.variant` in its committed `rig.yaml`: `warn` or
`block`. rig installs `workflow-{variant}.yml` for a CI slot and refuses a variant the slot does not
ship (`riglib/actions/runner.py`:688-692); without a variant it falls back to `workflow.yml`.
`rig apply commit` lands workflow files through the tracked-files PR flow. `--enforce repo`
reads the same key from `rig.yaml` at the repo root, so the hooks and CI never disagree. A repo
without the key runs in `warn`. The global rig default stays `warn` permanently; blocking is always a
line in the repo's own `rig.yaml`, which is what `--enforce repo` reads. (rig's `tier` field is only
recorded and does not change a workflow, `docs/config-schema.md`:455, so the mode is carried by the
variant.)

### 8.2 Git hooks

Two fragments in agent-tools `git-hooks/global-dispatcher/global-hooks.d/`, installed by rig's
existing dispatcher to `~/.config/git/global-hooks.d/` for every repo on the machine:

- `pre-commit/30-text-lint`: `text-lint diff --staged`.
- `commit-msg/30-text-lint`: `text-lint check --profile commit --cleanup editor --enforce repo "$1"`.

Both: `text-lint` not on PATH → one warning, exit 0 (as `10-secret-scan` does with a missing
gitleaks); exit 2 → print the error, exit 0, because CI repeats the check; exit 1 → block. They are
not `global-hook-protected`, so `GLOBAL_HOOKS_SKIP=text-lint` and `.githooks-skip` work locally;
CI is the gate that cannot be skipped. A repo opts out of the fragments with
`git_hooks.dispatcher.fragments.text-lint.enabled: false`.

### 8.3 CI slot

agent-tools `ci/text-lint/` ships `workflow-warn.yml`, `workflow-block.yml`, `workflow.yml` (the
same as `workflow-warn.yml`, for a repo that enables the slot without a variant) and
`text-lint.sh`. It triggers on `pull_request_target` with the activity types `opened`, `edited`,
`synchronize`, `reopened` and `ready_for_review` (as `pr-title-lint` does), so editing a PR title
or body reruns it, and so the workflow file, its `TEXT_LINT_ENFORCE` value and `text-lint.sh` always
come from the base branch: a PR that edits any of them, or `rig.yaml`, cannot relax the check that
runs on it. The PR's content is only read as data, never executed: the job checks out the base
branch, fetches the PR head commit, and passes it to text-lint, which reads blobs with `git show`.
It installs uv, Python 3.12, the pinned text-lint tag and the pinned Vale release
(checksum-verified, as slop-cli's `tests.yml` does), then:

1. `text-lint diff --base "$BASE_SHA" --head "$HEAD_SHA"` on the PR's changes (`--head` reads the
   files from that commit instead of the working tree);
2. `text-lint check --profile commit` on the message of every commit in `$BASE_SHA..$HEAD_SHA`,
   each read separately with `git log -1 --format=%B`, so a commit that skipped the local
   commit-msg hook is still checked;
3. builds an input@1 document from `$GITHUB_EVENT_PATH` (title and body, stdlib Python, no API
   call, like `pr-title-lint`) and runs `text-lint check --profile pr --laya-mode ci --input-json -
   --format json`, because a CI runner has no Laya model (locally, the `pr` profile requires Laya);
4. writes findings and applied suppressions to the job summary, with the line
   `laya: not run (ci runner)`.

All three checks get `--enforce` from the base branch's workflow (`TEXT_LINT_ENFORCE`: `warn` in
`workflow-warn.yml` and `workflow.yml`, `block` in `workflow-block.yml`), not `--enforce repo`; rig
changes the workflow file only when it lands a new `rig.yaml` on the default branch. The warn
workflow always exits 0; `workflow-block.yml` exits 1 when any of the three checks blocks. Inline
suppressions and `.text-lint.yaml` are read from the PR head, because they are part of the change
under review, and every applied suppression is listed in the job summary.

### 8.4 rig-cli

rig-cli adds the new slot and fragments to its known lists (`docs/config-schema.md`:513-585), adds
`ci.items.text-lint: {enabled: true, variant: warn}` to the defaults it writes, reports a repo
without the slot in `rig status`, and documents the switch. `tools.items.text-lint` replaces
`slop-check`.

## 9. Rollout

1. **text-lint 0.2.0**: rename, package move, `slop-check` shim. No behavior change.
2. **text-lint 0.3.0**: native engine, profiles, input@1 / report@1, the ported tg-cli and
   agent-tools rules with parity tests, suppressions, `.text-lint.yaml`.
3. **text-lint 0.4.0**: `diff`, extraction, `selftest`, golden and budget tests.
4. **Consumers switch**: tg-cli (gates and voice path), agent-tools hooks and skill, task-cli
   (warning, plus `task classify-server start`), review-cli. Each deletes its own copy in the same
   PR that switches. The tg-cli part is **on hold** until the owner answers whether tg-cli itself
   moves to Python (section 12); deleting the `decision-request-format` hook waits with it, while
   the chat dictionary gate switches without waiting.
5. **Warn everywhere**: agent-tools ships the fragments and the CI slot; rig-cli adds the defaults;
   `rig apply` installs them for every repo in `warn`. For 14 days the CI summaries and the local
   hook log (`~/.cache/text-lint/hook.jsonl`: rule id, path, line, verdict; no text) are triaged
   weekly; a rule whose sampled findings are mostly false positives is tuned or disabled with a
   reason, as ADR 0001 did for ai-tells.
6. **Block, repo by repo**: one `rig.yaml` PR per repo sets `variant: block`, in this order:
   text-lint, agent-tools, rig-cli, tg-cli, task-cli, review-cli, then the remaining repos. A repo
   flips only when it has no open false-positive report against a rule. The same step lifts the
   warning cap on Vale and aiw-ru findings in `tg-bot`, `as-user` and `spoken` (the owner's
   decision 1), sets `fail_on: warning` in `tg-bot` and `as-user` so those findings block (4.5),
   and makes task-cli's `prose` gate blocking.
7. **Shim removal** under the condition in 4.1.

Existing repos do not break: only changed lines are checked, the mode starts at `warn`, a missing
binary or engine only warns in hooks, and fixture directories are excluded by default.

## 10. Testing

### 10.1 Every rule fires (positive controls)

- A test enumerates every enabled rule id (native packs, the `TextLint` Vale style) and fails when
  one has no positive example; another runs every example and fails when a `flag` example yields no
  finding of that rule or a `pass` example yields one.
- `text-lint selftest` runs the same positive controls through the real engines and exits 1 when
  any rule reports nothing on its own `flag` examples. text-lint's CI runs it, and its output is the
  proof attached to any "the checker works" claim.

### 10.2 Golden tests per profile

`tests/golden/<profile>/<case>.{txt,md,json}` with `<case>.expected.json` holding only
`rule_id`, `line`, `col`, `severity` and the verdict (messages change too often to pin).
`TEXT_LINT_UPDATE_GOLDEN=1` rewrites them; the PR diff shows the change. Every profile has at
least one clean case and one case per pack it runs.

### 10.3 Parity before deletion

Before a consumer deletes its copy, its test cases are exported into text-lint as
`tests/parity/<consumer>/<gate>.jsonl` (`{"input": …, "expected": "block"|"pass", "rule": …}`):
tg-cli's `dict-gate`, `cjk-guard`, `mixed-script-guard`, `escalation-format`, `intent-gate` and
`message-structure` tests, and agent-tools' `test_chat_dictionary_gate.py` including its real-DICT
case. text-lint must give the same verdict on every line. A difference is fixed or recorded as an
intended change in the PR body.

### 10.4 Diff, extraction and suppression tests

Temporary git repos with staged changes in Markdown, TSX, Python, JSON, YAML, `.po`, Swift and
Kotlin files: findings appear only on changed lines; an unchanged slop line yields no finding;
excluded paths yield none; a suppression with a reason hides its finding and appears in `suppressed`; one
without a reason yields `TextLint.SuppressionWithoutReason`.

### 10.5 Budget and offline tests

- `tests/test_budget.py`: `tg-bot` and `as-user` on a realistic input@1 document (Russian
  `title`, `subagent` and `harness_title` fields plus a 4096-character mixed Russian and English
  `body`), p95 of 5 runs at most 1.5 s with a warm fake Laya socket; all fields go through one Vale
  and one aiw-ru call. `spoken` on 1000 characters, native rules p95 at most 250 ms. Runs in CI.
- Laya client tests with a fake socket: a warm request that does not answer within 1.5 s fails
  with exit 2; a cold start waits at most 20 s for the start command, then sends one request.
- The integration test runs under `unshare -rn` (no network) where the runner allows it.

### 10.6 Consumer tests

- Each consumer: a contract test against the real binary (positive control) and unit tests with a
  fake `text-lint` that returns 0, 1 and 2.
- agent-tools: fragment tests with a fake binary for each exit code in each mode.

Heavy runs follow this repo's rule: one test file locally under `nice -n19 ionice -c3`, the full
suite in CI or on Runpod.

## 11. Risks

| Risk | Mitigation |
|---|---|
| False positives teach people to skip the tool | changed lines only; 14-day warn phase with weekly triage; disable with a reason |
| Slower Telegram sends | `tg-bot` and `as-user` budget tests (1.5 s p95 warm); one cold Laya start of at most 20 s after 15 min idle; `chat` runs native rules only |
| A broken text-lint blocks every commit or send | hooks warn on exit 2 and CI repeats the check; in tg-cli exit 2 means a configuration error or an unreachable Laya, and the refusal names which |
| Local and CI versions differ | CI pins a tag and is authoritative; both print `version` |
| Suppressions as a bypass | a reason is required; every suppression is listed in the report and the CI summary |
| Noise from debug output in code (`console.log`) | identifier and one-word filters, per-repo `ui.calls`, warn phase |
| aiw-ru has one maintainer | pinned range; Russian gaps go into `TextLint` rules, not a fork (ADR 0001) |
| Rename breaks links | Forgejo redirect; the shim; updated rig and skill entries |
| Memory on the shared workstation | one Vale and one aiw-ru process per run (65-71 MB peak measured); no resident text-lint server; one shared Laya server (about 2 GB) that exits after 15 min idle |

## 12. Decisions

The owner answered in tg#22710 (reply to tg#22655):

> «Laya обязательно, локально. А почему форматы из тг убираем? Эмоджи в тг боте ок кстати. Не ок при общении от лица пользователя, надо различать. Вопросы 1A+словарь и пр 2A, 3A, tg-cli должен быть на питоне тоже!»

Owner decisions:

1. **Slop findings in Telegram sends (1A plus the dictionary).** Every Telegram send runs the full
   set: native rules (dictionary, scripts, formats), Vale and aiw-ru. Vale and aiw-ru findings are
   warnings during the warn phase and block after it.
2. **Warn phase (2A).** 14 days with weekly triage, then blocking repo by repo.
3. **Contract (3A).** One Python implementation behind the JSON CLI contract.
4. **Laya is mandatory and local.** A check that uses Laya asks the local task-cli classify
   server; nothing goes to the network. text-lint starts the server on demand when the socket does
   not answer, and exits 2 when Laya still cannot be reached (4.3, 4.7).
5. **Identity.** The single Telegram profile of the first draft is replaced by `tg-bot` (bot sends, emoji allowed) and `as-user`
   (text sent as the owner: no emoji, no bot formatting, natural first-person speech). The
   consumer picks the profile from the send path; there is no flag to choose it (4.5).
6. **Formats stay on every send.** The decision, problem and report checks keep running on every
   `tg` send exactly as today; only their code moves into text-lint, so PRs, issues and reviews
   share the rules (2.6).
7. **On hold: "tg-cli on Python".** Main has asked the owner whether this means a full port of
   tg-cli to Python. The tg-cli tasks of the plan (Tasks 12 and 13) wait for the answer; this spec
   does not design a port.

Controller rulings, recorded so the owner can undo them:

| # | Ruling | Cost if wrong |
|---|---|---|
| R1 | Laya starts on demand through `task classify-server start` (5.5); text-lint runs it once on a refused or absent socket and retries. task-cli already owns the spawn, the singleton flock and the idle exit; text-lint must not import tasklib. | one more rig unit later |
| R2 | Laya budget: warm request timeout 1.5 s; cold start wait up to 20 s; one request after start, any failure exit 2. Sends: 1.5 s p95 warm plus a one-off cold start of at most 20 s after 15 min idle. | a slow first send after idle, or a refused send on a slow cold start; the numbers are tuned in one place (4.7) |
| R3 | Laya is used by `tg-bot` (`asks_reader`, slop), `as-user` and `spoken` (slop, `first_person`), `issue` and `pr` (slop); not by `commit`, `ui-strings`, `prose`, `chat` or `diff`. | add `laya: required` to those profile files later |
| R4 | The CI PR-body check runs `--profile pr --laya-mode ci`, reported as `laya: not run (ci runner)`; `--laya-mode ci` needs `CI=true`. | a PR body written outside the skill gets to CI with no Laya verdict |
| R5 | The flag that turned Laya off and the `TG_LAYA` off switch are removed; `TEXT_LINT_LAYA_SOCKET` and `TEXT_LINT_LAYA_TIMEOUT_MS` stay for tests and non-default sockets. | a Laya outage refuses sends until the server is fixed |
| R6 | Emoji rules, `TextLint.DoneEmoji` included, are off in `tg-bot` (the status-report phrase is still caught by `TextLint.StatusReport` / `RuStatusReport`). `as-user` adds `AsUser.Emoji` and `AsUser.BotFormatting` (errors) and `AsUser.NotFirstPerson` (Laya `first_person` below 0.5, error, blocking after the warn phase). | emoji-only status replies from the bot go unflagged |
| R7 | Profile selection in tg-cli, for when Tasks 12-13 resume: bot sends → `tg-bot`; `tg user send`, `tg call --say` → `as-user`; duplex replies → `spoken`. Chosen in code, no CLI flag. | a send path checked with the wrong identity until its call site is changed |
