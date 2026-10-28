# text-lint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn slop-cli into text-lint, the one offline implementation of text checks, switch tg-cli, agent-tools, task-cli and review-cli to it, and have rig run it on changed lines in every repo through hooks and CI.

**Architecture:** One Python package (`text_lint`) with three engines (Vale, aiw-ru, a native YAML-driven engine) behind profiles. Consumers run the `text-lint` binary with an input@1 JSON document on stdin and read a report@1 JSON document. Enforcement is two dispatcher fragments and one CI slot in agent-tools, provisioned by rig; the per-repo switch is `ci.items.text-lint.variant` in `rig.yaml`.

**Tech Stack:** Python ≥ 3.12, Vale 3.24.0, aiw-ru ≥ 2.5 < 3, ast-grep-py 0.51, PyYAML, pytest 8; Bun/TypeScript (tg-cli); POSIX sh (hook fragments); Forgejo Actions (GitHub Actions syntax).

**Spec:** `docs/specs/2026-10-17-text-lint-design.md`

## Global Constraints

- Exit codes everywhere: 0 = verdict `pass` or `warn`, 1 = verdict `block`, 2 = usage, input, configuration or required-engine error.
- Report schema id `text-lint/report@1`; input schema id `text-lint/input@1`.
- No network at run time; aiw-ru always gets `--no-model`. Laya is mandatory and local: profiles `tg-bot`, `as-user`, `spoken`, `issue`, `pr` require it; on a refused or absent socket text-lint runs `task classify-server start` once and retries; Laya still unreachable → exit 2 naming Laya. No flag or env var turns Laya off; `--laya-mode ci` (only with `CI=true`, else exit 2) skips it on CI runners and reports `laya: not run (ci runner)`. `TEXT_LINT_LAYA_SOCKET` and `TEXT_LINT_LAYA_TIMEOUT_MS` stay for tests.
- Profiles: `prose`, `issue`, `pr`, `commit`, `tg-bot`, `as-user`, `chat`, `spoken`, `ui-strings`; `fail_on` is `warning` for all but `tg-bot`, `as-user`, `chat`, `spoken` (`error`); after the warn phase (Task 17 step 4) `tg-bot` and `as-user` move to `warning`. The consumer picks `tg-bot`/`as-user`/`spoken` from the send path in code; no flag chooses it.
- In `tg-bot`, `as-user` and `spoken`, a missing Vale or aiw-ru is reported as skipped and their findings are capped at `warning` during the warn phase. Emoji rules (`TextLint.DoneEmoji` included) are off in `tg-bot`; `as-user` and `spoken` flag emoji and bot formatting as errors (`AsUser.*`).
- Suppression reasons: at least 10 characters after `--` (inline) or in `reason:` (`.text-lint.yaml`); `TextLint.SuppressionWithoutReason` cannot be suppressed.
- Enforce: `--enforce warn|block|repo`; `repo` reads `ci.items.text-lint.variant` from the repo's `rig.yaml`, absent → `warn`; default `repo` for `diff`, `block` for `check`.
- Budgets: `tg-bot` and `as-user` p95 ≤ 1.5 s for 4096 characters with Laya warm; `spoken` native rules p95 ≤ 250 ms for 1000 characters. Laya: warm request timeout 1.5 s (measured ~0.2 s); cold start wait ≤ 20 s (measured ~3 s), once after 15 min idle; after the server is up one request, any failure exit 2.
- Dictionary path order: `--dict`, `TEXT_LINT_DICT`, `TG_DICT_PATH`, `~/.claude/DICT.json`; explicit missing → exit 2; default missing → no `Dict.*` rules.
- Machine rule: locally run one test file at most under `nice -n19 ionice -c3`; full suites in PR CI or via `runpod-run`. One `review` at a time under `flock /tmp/disk-session-review.lock`.
- Every PR: ticket first (`task new`), worktree (`rig worktree create`), TDD red first, conventional commits, version bump on behavior change, prose through `text-lint check` (or `slop-check` before Task 1 lands).

## Review Focus

1. **Empty and whitespace-only input** (an empty Telegram caption, a staged file with only deleted lines): expect verdict `pass`, exit 0, engines `skipped`, never exit 2. Test added to Task 2 and Task 8.
2. **Malformed DICT.json reached through `TG_DICT_PATH` set in `~/.config/tg-cli/.env`**: tg-cli must refuse exactly as today, and the message must name the file. Test added to Task 3 and Task 12.
3. **A commit that renames a file without changing text** (`git diff --diff-filter=AM` excludes `R`, but a rename with edits shows as `R0xx`): only the edited lines are checked. Test added to Task 8.
4. **HTML entities and `<code>` in tg-cli captions** (`&lt;скамейка&gt;` inside `<code>`): masked like tg-cli's `stripForDictionary`, so no finding; outside `<code>` it is found with the decoded text as `match`. Test added to Task 3.
5. **A repo whose `rig.yaml` is invalid YAML**: `--enforce repo` falls back to `warn` with one stderr line instead of exit 2, so a broken config never blocks commits. Test added to Task 7.
6. **Laya cold start and failure**: a send after 15 min idle starts the server once and succeeds; a start that fails exits 2 naming Laya, never a silent pass; `--laya-mode ci` outside `CI=true` is exit 2. Tests added to Task 5 and Task 15a.
7. **Identity profiles**: the same emoji text passes in `tg-bot` and fails in `as-user`; no CLI path lets a sender pick the profile. Tests added to Task 6.

---

## Repo: text-lint (today ultrabricks/slop-cli)

### Task 1: Rename to text-lint, with a `slop-check` shim (0.2.0)

**Files:**
- Move: `slop_cli/` → `text_lint/`; `slop_cli/vale/` → `text_lint/packs/vale/`; `styles/SlopCheck/` → `styles/TextLint/`
- Modify: `pyproject.toml` (name `hyper-text-lint`, scripts `text-lint = "text_lint.cli:main"`, `slop-check = "text_lint.shim:main"`, package-data path), `install.sh`, `bin/`, `README.md`, `AGENTS.md`, `rig.yaml` (`task.forgejo.repo: ultrabricks/text-lint`), `tests/**`, `docs/adr/0001-*.md` (fix the DICT.json sentence at line 12)
- Create: `text_lint/shim.py`, `tests/test_shim.py`

**Interfaces:**
- Produces: `text_lint.cli.main(argv: list[str] | None = None) -> int` (0.1 behavior under `check`); `text_lint.shim.main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing tests** in `tests/test_shim.py`:
  - `test_shim_passes_legacy_args`: `shim.translate(["--context", "tg", "--lang", "ru", "-"]) == ["check", "--context", "tg", "--lang", "ru", "-"]` (in 0.2.0 `text-lint check` still takes slop-check 0.1's arguments; Task 2 changes the translation).
  - `test_shim_maps_ignore_prefix`: `["--ignore", "SlopCheck.Hedging"]` → `["check", "--ignore", "TextLint.Hedging"]`.
  - `test_shim_install_skill`: `shim.translate(["install-skill"]) == ["install-skill"]` (no `check` prefix), and `shim.main(["install-skill"])` with `HOME` pointed at a temp dir writes `.agents/skills/.blurbs/` and returns 0, as `install.sh`:42,169 calls it.
  - `test_shim_prints_deprecation`: stderr contains `slop-check is deprecated; use text-lint check`.
  - `test_shim_runs_real_cli` (fake engines from `tests/fakes/`): `shim.main(["--context", "tg", path])` on a file with `Great catch` returns 1 and prints `TextLint.Sycophancy`.
- [ ] **Step 2: Run** `nice -n19 ionice -c3 uv run --no-project --with 'pytest>=8,<9' pytest tests/test_shim.py -q`. Expected: FAIL, `ModuleNotFoundError: text_lint`.
- [ ] **Step 3: Move the package** with `git mv`, rename the style, move `tests/fixtures/rule_samples.tsv` to `text_lint/packs/vale/samples.tsv` (package data, read by the tests through `importlib.resources`), update imports, rule ids `SlopCheck.*` → `TextLint.*` in `samples.tsv` and tests; implement `text_lint/shim.py` with `translate(argv) -> list[str]` and `main`; `text-lint check` is today's `slop-check` argument set; bump `__version__` to `0.2.0`.
- [ ] **Step 4: Run** the shim test file (PASS), then push and let PR CI run the full suite.
- [ ] **Step 5: Rename on the forge** after merge: Forgejo settings → rename to `text-lint`; verify `git ls-remote https://git.hyperide.ai/ultrabricks/slop-cli.git` still answers (redirect); update the local remote and move the checkout to `~/dev/text-lint`; update `~/.config/rig/config.yaml` `tools.items` (`text-lint: {repo: ~/dev/text-lint, brew: [vale]}`) and run `rig apply commit --only tools`; `text-lint --version` prints `text-lint 0.2.0`.
- [ ] **Step 6: Commit** `refactor!: rename slop-cli to text-lint, keep slop-check as a shim`.

### Task 2: Report@1, input@1, profiles, verdict and exit codes

**Files:**
- Create: `text_lint/report.py`, `text_lint/inputs.py`, `text_lint/profiles.py`, `text_lint/profiles/{prose,issue,pr,commit,tg-bot,as-user,chat,spoken,ui-strings}.yaml`, `text_lint/schema/report-v1.json`, `text_lint/schema/input-v1.json`, `tests/test_report.py`, `tests/test_inputs.py`, `tests/test_profiles.py`
- Modify: `text_lint/check.py`, `text_lint/cli.py`, `text_lint/shim.py` (translation to the new flags), `tests/test_shim.py`, `text_lint/engines.py` → `text_lint/engines/{vale,aiwru,laya}.py`, `pyproject.toml` (add `PyYAML>=6,<7`; package data `profiles/*.yaml`, `schema/*.json`, `packs/**/*`, `extract/rules/*.yml`, so later tasks need no packaging change), `.github/workflows/tests.yml` (`TEST_EXTRA_DEPS: "aiw-ru>=2.5,<3 PyYAML>=6,<7 jsonschema>=4,<5"`, because CI runs `uv run --no-project` and does not read `pyproject.toml`; plus a `wheel-smoke` step: `uv build --wheel`, install the wheel into a fresh venv under `$RUNNER_TEMP`, `cd "$RUNNER_TEMP"` and run `printf 'Great catch\n' | text-lint check --profile prose --lang en`, expecting exit 1 with `TextLint.Sycophancy`; Task 9 changes the command to `text-lint selftest`)

**Interfaces:**
- Produces:
  - `Finding` dataclass: `rule_id: str, engine: str, severity: Literal["error","warning","suggestion"], line: int, col: int, span: tuple[int,int], match: str, message: str, suggestion: str | None = None, field: str | None = None, path: str | None = None, key: str | None = None, gate: bool = True`; `as_dict() -> dict` (omits `gate`).
  - `Unit` dataclass: `text: str, raw: str, format: Literal["plain","markdown","html"], field: str | None, path: str | None, key: str | None = None, gate: bool = True, line_offset: int = 0, raw_map: list[tuple[int, int]] | None = None, norm_to_raw: list[int] | None = None` (`text` is the normalized text that `pattern` and the Vale/aiw-ru engines read; `raw` the field as sent; `semantic_text: str | None = None` and `semantic_to_raw: list[int] | None = None` provide normalized markup through `Unit.semantic`, which the script, spoken and message kinds read; `raw_map[i]` is the 1-based source `(line, col)` of `raw[i]`, `None` meaning positions follow from `raw` and `line_offset`; `norm_to_raw[j]` is the offset in `raw` of `text[j]`, `None` meaning identity); `Unit.position(offset: int, *, space: Literal["text","semantic","raw"]) -> tuple[int, int]` (a `text` offset goes through `norm_to_raw` first; a `semantic` offset uses `semantic_to_raw`); `Meta` dataclass: `tag: str | None, agent_authored: bool, automated: bool, send_format: Literal["plain","markdown","html"] | None, attachments: int = 0, table: bool = False` (`send_format` is the sender's explicit format flag, independent of each field's detected `format`).
  - `inputs.parse_input_json(raw: str) -> tuple[list[Unit], Meta]` (raises `InputError`); `inputs.unit_from_text(text: str, fmt: str) -> Unit`; `inputs.unit_from_api_text(raw: str, fmt: str) -> Unit` applies the first input@1 field's leading decoded BOM contract while preserving original source coordinates.
  - `Profile` dataclass: `name, engines: dict[str, EngineSpec], packs: list[str], fail_on: str, markup: str`; `EngineSpec`: `enabled: bool, optional: bool, cap: str | None, options: dict`; `profiles.load_profile(name: str) -> Profile` (raises `ConfigError`).
  - `report.verdict(findings: list[Finding], fail_on: str, enforce: Literal["warn","block"]) -> Literal["pass","warn","block"]`; `report.build_report(*, profile, enforce, findings, suppressed, engines, score, intent=None) -> dict`; `report.exit_code(report: dict) -> int`.
  - `check.check_units(units: list[Unit], meta: Meta, profile: Profile, *, enforce: str, lang: Literal["auto","en","ru"] = "auto", ignore: Sequence[str] = (), fail_on: str | None = None, laya_mode: Literal["required","ci"] = "required", suppress=None, dict_path=None, from_input_json=False, environment=None) -> dict` (`lang` keeps today's `--lang` routing: `en` skips aiw-ru, `ru` drops `ai-tells` on Russian text; `laya_mode` is `required` by default and `ci` for `--laya-mode ci`, which the CLI accepts only with `CI=true` in the environment; the profile alone decides which Laya questions are asked).

- [ ] **Step 1: Write the failing tests:**
  - `test_verdict_levels`: one `warning` finding with `fail_on="warning"` and `enforce="block"` → `"block"`; same with `enforce="warn"` → `"warn"`; a `suggestion` with `fail_on="warning"` → `"warn"`; no findings → `"pass"`; only `laya.SlopProbability` → `"pass"`; only an `error` finding with `gate=False` → `"warn"`.
  - `test_unit_position_spaces`: for an html unit `"<p>a</p><code>ка日eat</code>"` with a `raw_map`, `position(raw.index("日"), space="raw")` gives the mapped source position of `日`; `position(j, space="text")` for the normalized offset of `a` gives the source position of `a`; neither indexes out of range.
  - `test_fields_batched`: with fake `vale` and `aiw-ru` that count invocations, an input@1 document with `title`, `subagent`, `body` and `harness_title` fields, all Russian → one Vale call and one aiw-ru call, and each finding carries the `field` it came from.
  - `test_exit_codes`: `{"verdict": "pass"} → 0`, `"warn" → 0`, `"block" → 1`.
  - `test_report_matches_schema`: `build_report(...)` validates against `text_lint/schema/report-v1.json` (use `jsonschema` only in tests).
  - `test_parse_input_json`: two fields and `meta.tag == "decision"` parse; `"gate": false` on a field gives `Unit.gate is False`; a missing `schema` key raises `InputError`; `format: "rtf"` raises `InputError`.
  - `test_empty_input_passes` (Review Focus 1): `check_units([unit_from_text("  \n", "plain")], ...)` → verdict `pass`, every engine `status == "skipped"`.
  - `test_laya_mode_ci_needs_ci_env`: CLI `check --laya-mode ci` without `CI=true` → exit 2 with a usage message; with `CI=true` → the report's `engines.laya` is `{"status": "not run", "reason": "ci runner"}`.
  - `test_lang_override_kept`: the existing `tests/test_cli.py` test of `--lang en` / `--lang ru` (`test_lang_en_skips_aiwru_and_lang_ru_drops_ai_tells`) still passes through `check_units(..., lang=...)`.
  - `test_profiles_load`: every file in `text_lint/profiles/` loads; `load_profile("tg-bot").engines["vale"].cap == "warning"` and `.optional is True`; `load_profile("tg-bot").engines["laya"].optional is False`; `load_profile("as-user").engines["laya"].options["questions"]` contains `first_person`; `load_profile("commit").engines["laya"].enabled is False`; unknown name raises `ConfigError`.
  - In `tests/test_shim.py`, replace `test_shim_passes_legacy_args` with `test_shim_maps_context`: `shim.translate(["--context", "tg", "-"]) == ["check", "--profile", "tg-bot", "--fail-on", "suggestion", "-"]`; `docs` → `prose`, `issue` → `issue`, `pr` → `pr`, no context → `prose`; `test_shim_runs_real_cli` stays and must still return 1 for `Great catch`.
- [ ] **Step 2: Run** each new test file alone. Expected: FAIL on missing modules.
- [ ] **Step 3: Implement.** Split `engines.py` into `engines/vale.py` and `engines/aiwru.py` without behavior change except `--no-model`; `check.check_units` batches all of its units into one Vale call and one aiw-ru call (units joined with blank lines plus an offset map back to unit and field), so a multi-field Telegram message costs one process of each, and applies the engine `cap`; CLI gains `--profile`, `--input-json`, `--enforce`, `--fail-on` and drops `--context` (the shim translates it); `check` without `--profile` uses `prose`. Profile YAML values exactly as the spec table in §4.5.
- [ ] **Step 4: Run** the new files one by one (PASS); PR CI runs the full suite including the old integration tests.
- [ ] **Step 5: Commit** `feat: profiles, input@1 and report@1 with verdict-based exit codes`.

### Task 3: Native engine, `pattern` kind and the DICT.json loader

**Files:**
- Create: `text_lint/engines/native.py`, `text_lint/native/__init__.py` (kind registry), `text_lint/native/pattern.py`, `text_lint/native/dictionary.py`, `text_lint/schema/native-rule-v1.json`, `tests/test_native_pattern.py`, `tests/test_dictionary.py`, `tests/parity/tg/dict-gate.jsonl`, `tests/parity/agent-tools/chat-dictionary-gate.jsonl`, `tests/test_parity.py`
- Modify: `text_lint/text.py` (add `strip_html(text) -> tuple[str, list[int]]` returning stripped text and an offset map)

**Interfaces:**
- Consumes: `Unit`, `Meta`, `Finding` (Task 2).
- Produces:
  - `NativeRule` dataclass: `id, kind, severity, profiles: list[str], message: str, params: dict, examples: dict[str, list[str]], replacement: str | None, why: str | None`.
  - `native.KINDS: dict[str, Callable[[NativeRule, Unit, Meta, str], list[Finding]]]` (last argument: whole input text, for `only_if_script`).
  - `native.load_pack(directory: Path) -> list[NativeRule]` (schema-validated, raises `ConfigError`).
  - `dictionary.resolve_path(cli: str | None, env: Mapping[str,str]) -> tuple[Path, bool] | None` (path, explicit); `dictionary.load(path: Path, explicit: bool) -> list[NativeRule]`.
  - `engines.native.run(units: list[Unit], meta: Meta, rules: list[NativeRule]) -> list[Finding]`.

- [ ] **Step 1: Export the parity corpora**: a one-off script (not committed) turns every case in `~/dev/tg-cli/tests/dict-gate.test.ts` and `~/dev/agent-tools/tests/test_chat_dictionary_gate.py` into JSONL lines `{"input": {...input@1...}, "expected": "block"|"pass", "rule": "Dict.<id>"|null, "dict": "<fixture dict path>"}`; commit the JSONL and the fixture dictionaries under `tests/parity/`.
- [ ] **Step 2: Write the failing tests:**
  - `test_boundary_rewrite`: rule `\bскаме[йе]` flags `"на скамейке"` and not `"наскамейке"`.
  - `test_only_if_cyrillic`: a rule with `only_if_cyrillic: true` is silent on an all-Latin input and fires when any gating field has Cyrillic.
  - `test_only_if_cyrillic_ignores_non_gating_field`: Cyrillic-only rule matching `fork`, body `"Create a fork"` (gating), `harness_title` `"Проверка"` with `gate: false` → no finding; with the title made gating → one finding on the body.
  - `test_html_code_masked` (Review Focus 4): an html field `<code>&lt;скамейка&gt;</code>` → no finding; `&lt;скамейка&gt;` outside code → finding with `match == "скамейка"` and `col` pointing into the raw field.
  - `test_dict_resolution`: `TEXT_LINT_DICT` wins over `TG_DICT_PATH`; explicit missing path → `ConfigError`; default missing → `None`.
  - `test_malformed_dict_names_file` (Review Focus 2): invalid JSON at an explicit path → `ConfigError` whose message contains the path; CLI exit 2.
  - `test_parity[tg/dict-gate]`, `test_parity[agent-tools/chat-dictionary-gate]`: every JSONL line gives its `expected` verdict.
- [ ] **Step 3: Run** `tests/test_native_pattern.py` alone. Expected: FAIL.
- [ ] **Step 4: Implement** the registry, the `pattern` kind (port of `toUnicodeBoundaryPattern`, `features/cli/dict-gate.ts`:128; flags `u` forced), the loader for DICT.json version 1 (ids `Dict.<id>`, severity `error`, `suggestion` = `replacement`), and `strip_html` (port of `stripForDictionary`, `dict-gate.ts`:289). Wire `native` into `check_units` with packs from the profile plus `Dict.*` for profiles listing `dict`.
- [ ] **Step 5: Run** the three new test files one by one. Expected: PASS.
- [ ] **Step 6: Commit** `feat: native rule engine with DICT.json parity`.

### Task 4: `stray-cjk` and `mixed-script` kinds

**Files:**
- Create: `text_lint/native/script.py`, `text_lint/packs/native/script/{StrayCjk,MixedScript}.yml`, `tests/test_native_script.py`, `tests/parity/tg/{cjk-guard,mixed-script-guard}.jsonl`

**Interfaces:**
- Consumes: `NativeRule`, `native.KINDS` (Task 3).
- Produces: kinds `stray-cjk` (params `dominant_share: 0.5`, `dominant_min: 3`) and `mixed-script`; rule ids `Script.StrayCjk`, `Script.MixedScript`.

- [ ] **Step 1: Export** the cases of `~/dev/tg-cli/tests/cjk-guard.test.ts` and `tests/mixed-script-guard.test.ts` into the two JSONL files.
- [ ] **Step 2: Write the failing tests:** `test_stray_cjk_flags_lone_codepoint` (`"Проверил 的тесты"`, a lone CJK codepoint stuck to a Cyrillic letter, → one finding, `match == "的"`; `"Проверил 的 тесты"` with spaces on both sides → none, as tg-cli's `findStrayCjk` returns `null` for it); `test_cjk_dominant_text_passes` (`"日本語のテキストです"` → none); `test_mixed_script_allows_slang` (`"заpushил"` → none); `test_mixed_script_flags_garbage` (a token with two Cyrillic↔Latin transitions from the tg-cli tests → one finding); `test_parity[tg/cjk-guard]`, `test_parity[tg/mixed-script-guard]`.
  Also `test_cjk_inside_html_code_blocks` (through `check_units` with input@1: an `html` field `<code>ка日eat</code>` → `Script.StrayCjk`, because script kinds read `Unit.semantic` as tg-cli's guard reads the raw caption, `tests/cjk-guard.test.ts`:87).
- [ ] **Step 3: Run** `tests/test_native_script.py`. Expected: FAIL.
- [ ] **Step 4: Implement** as ports of `findStrayCjk` (`features/cli/cjk-guard.ts`:98) and `findMixedScriptTokens` (`mixed-script-guard.ts`:142), returning every hit, not only the first.
- [ ] **Step 5: Run** the test file. Expected: PASS.
- [ ] **Step 6: Commit** `feat: stray-CJK and mixed-script rules ported from tg-cli`.

### Task 5: Formats, intent, long-message advisory, one mandatory local Laya client, `tg-bot` profile

**Files:**
- Create: `text_lint/native/formats.py`, `text_lint/native/intent.py`, `text_lint/native/structure.py`, `text_lint/packs/native/formats/{Decision,Problem,Report}.yml`, `text_lint/packs/native/structure/LongUnstructured.yml`, `tests/test_native_formats.py`, `tests/test_native_intent.py`, `tests/parity/tg/{escalation-format,intent-gate,message-structure}.jsonl`, `tests/parity/agent-tools/decision-request-format.jsonl`
- Modify: `text_lint/engines/laya.py` (socket choice, timeout argument, on-demand start), `text_lint/profiles/{tg-bot,issue,pr}.yaml`, `tests/test_laya.py`

**Interfaces:**
- Consumes: `Meta.tag`, `Meta.agent_authored`, `Meta.automated`; `task classify-server start` (Task 15a).
- Produces:
  - kind `format` with `params.validator` in `decision|problem|report`; ids `Format.Decision`, `Format.Problem`, `Format.Report`; active only when `Meta.tag` matches. `format`, `intent` and `long-unstructured` are message kinds: they read `Unit.semantic` and run only on the field named `body`, or `caption` when no `body` is present (spec §4.6); `native.message_unit(units: list[Unit], *, from_input_json: bool) -> Unit | None` picks it, and when `from_input_json` is false (a file or stdin, one unit with no field name) returns that single unit.
  - `intent.classify(text: str, meta: Meta, laya_p: float | None) -> Intent` with `Intent(kinds: list[str], action: Literal["pass","follow-up","block"], reason: str | None, follow_ups: list[FollowUp], valid: Literal["decision","problem"] | None, hints: list[str])` and `FollowUp(kind: Literal["decision","problem"], quote: str)`; a `block` action also yields a finding `Intent.<reason>` (severity `error`).
  - kind `long-unstructured` → `Structure.LongUnstructured` (severity `suggestion`, plain text ≥ 500 characters, as `message-structure.ts`:12; skipped when `Meta.attachments > 0`, `Meta.table` is true or the body is not `plain`, as `shouldWarnLongUnstructuredPlainMessage` at :169-172).
  - `laya.advise(text: str, *, question: Literal["slop", "asks_reader", "first_person"], timeout_s: float = 1.5, start_wait_s: float = 20.0) -> dict` (raises `LayaUnavailable`, which the CLI turns into exit 2 naming Laya; on a refused or absent socket it runs `task classify-server start` once, waits up to `start_wait_s`, then sends one request) sharing one transport, choosing the socket with the highest version that accepts a connection; `question="slop"` sends slop-check's existing question and reads `answers.slop.noul`, `question="asks_reader"` sends tg-cli's `LAYA_QUESTIONS.asks_reader` (`features/cli/laya-socket.ts`:27-32) and reads `answers.asks_reader.noul`; `question="first_person"` sends "Does this read as a person writing in the first person, not as a bot or report?" and reads `answers.first_person.noul` (used by Task 6); the intent classifier only ever uses `asks_reader`.
  - `report["intent"] = {"kinds": list[str], "action": str, "reason": str | None, "follow_ups": list[{"kind": "decision"|"problem", "quote": str}], "valid": "decision"|"problem"|None, "hints": list[str], "laya_p": float | None}` in `tg-bot` runs (every field tg-cli's obligations wiring reads, `tg`:2819-2823).

- [ ] **Step 1: Export** the cases of `escalation-format.test.ts`, `escalation-gate-args.test.ts` (including :73, a compliant body without `--format html`), `intent-gate.test.ts`, `message-structure.test.ts` and agent-tools `decision-request-format` tests into JSONL (`expected` includes `intent.action` where the source asserts it).
- [ ] **Step 2: Write the failing tests:** `test_decision_missing_options` (a `--tag decision` body without options → `Format.Decision` with the missing section named in `message`); `test_format_inactive_without_tag`; `test_automated_skips_problem_report` (`meta.automated=True` → no `Format.Problem`); `test_bare_questions_block` (untagged agent-authored `"Should we deploy today? Which region should we use?"` → `action == "block"`, reason `malformed-decision`, as tg-cli's `classifyIntent` returns today); `test_intent_follow_up` (a valid `--tag report` body that also contains two questions → `action == "follow-up"`, `len(follow_ups) == 2`, each with `kind == "decision"` and the question as `quote`); `test_intent_valid_problem` (a problem-format body under `--tag problem` → `action == "pass"`, `valid == "problem"`; with `meta.automated=True` → `valid is None`); `test_laya_question_keys` (fake socket server records requests: `question="asks_reader"` sends the `asks_reader` question and parses `answers.asks_reader.noul`; `question="slop"` sends and parses `slop`; a reply carrying only `slop` yields `None` for `asks_reader`); `test_laya_timeout_respected` (fake socket server that sleeps 2 s, `timeout_s=1.5` → `LayaUnavailable` in under 2 s, CLI exit 2 naming Laya); all parity files.
  Also, through `check_units` with an input@1 document: `test_html_problem_passes` (a `--tag problem` html body with `<h3>` sections, `<hr>` separators and a list, valid by tg-cli's `validateProblemFormat`, → no `Format.Problem`, `intent.valid == "problem"`); `test_message_scope` (`tag: decision`, a valid decision body, an ordinary `title` field and a `harness_title` field containing a question → no `Format.*` or `Intent.*` finding, `intent.action == "pass"`).
  Also `test_tg_bot_asks_laya` (fake socket server: a `tg-bot` run on an agent-authored, untagged body sends one `asks_reader` and one `slop` request; a `prose` or `commit` run sends none; a `pr` run sends one `slop` request, and with `laya_mode="ci"` none and `engines.laya.status == "not run"`).
  In `tests/test_laya.py`, with a fake socket and a fake `task` on PATH that records calls: `test_laya_warm_call` (socket answers → one request, the fake `task` never called); `test_laya_cold_start` (no socket; the fake `task classify-server start` creates the fake socket and prints its path → the start command is invoked exactly once, the request succeeds, exit 0); `test_laya_start_fails` (the fake `task` exits 1 → CLI exit 2, stderr names Laya, no finding-free pass); `test_laya_mode_ci_outside_ci` (`--laya-mode ci` with `CI` unset → exit 2).
  Also `test_decision_requires_html` (a structurally compliant decision body in an `html` field with `meta.send_format` `null` or `plain` → `Format.Decision` naming `--format html`; the same body with `meta.send_format == "html"` → none).
  Also `test_human_report_not_gated` (`tag: report`, body `"Backup complete"`, `agent_authored=False`, `automated=False` → no `Format.Report`, no `Intent.*`, `intent.action == "pass"`, no Laya request; the same body with `agent_authored=True` → `Format.Report`); `test_decision_gated_for_everyone` (a malformed decision body with both flags false → `Format.Decision`).
  Also `test_message_unit_plain_input` (CLI: the dense 500-plus-character plain message from `tests/message-structure.test.ts`:87-93 piped on stdin to `text-lint check --profile tg-bot`, and the same text as the input@1 `body` field via `--input-json -`, each yield a `Structure.LongUnstructured` finding).
  Also `test_long_unstructured_skips_attachments` (the dense 500-plus-character plain caption from `tests/message-structure.test.ts`:87-93 → a `Structure.LongUnstructured` finding with `attachments=0`, none with `attachments=1`, none with `table=True`; assert on the finding, not the verdict, since it is a suggestion).
- [ ] **Step 3: Run** `tests/test_native_formats.py`. Expected: FAIL.
- [ ] **Step 4: Implement** as ports of `validateEscalationFormat` (`escalation-format.ts`:299), `validateProblemFormat` (`intent-gate.ts`:266), `validateReportFormat` (:298), `detectSignals` (:210), `classifyIntent` (:342), keeping their bilingual messages; `tg-bot.yaml` sets Laya `required: true`, `questions: [asks_reader, slop]`, `timeout_ms: 1500`, `start_wait_ms: 20000`, `max_chars: 2000`, and turns off the emoji rules (`TextLint.DoneEmoji`); `issue.yaml` and `pr.yaml` set Laya `required: true`, `questions: [slop]`.
- [ ] **Step 5: Run** the three new test files one by one. Expected: PASS.
- [ ] **Step 6: Commit** `feat: message format and intent rules, mandatory local Laya, tg-bot profile`.

### Task 6: `as-user`, `spoken`, `chat`, `commit`, `ui-strings` profiles, `AsUser.*` and `Spoken.Markup`

**Files:**
- Create: `text_lint/native/spoken.py`, `text_lint/native/as_user.py`, `text_lint/packs/native/spoken/Markup.yml`, `text_lint/packs/native/as-user/{Emoji,BotFormatting,NotFirstPerson}.yml`, `tests/test_native_spoken.py`, `tests/test_as_user.py`, `tests/test_commit_profile.py`
- Modify: the five profile files (`as-user`, `spoken`, `chat`, `commit`, `ui-strings`); `text_lint/inputs.py` (commit message cleanup)

**Interfaces:**
- Produces: kind `spoken-markup` → `Spoken.Markup` (severity `warning`; Markdown emphasis, headings, list markers, HTML tags, URLs, code spans, emoji); kinds `as-user-emoji` → `AsUser.Emoji` (error, any emoji codepoint) and `bot-formatting` → `AsUser.BotFormatting` (error; HTML tags, Markdown headings, emphasis, list markers, code spans, tag badges); `AsUser.NotFirstPerson` (error) from the Laya `first_person` answer below 0.5; profile `as-user` = native (dict, script, `AsUser.*`) + Vale `.tg` + aiw-ru `chat` + Laya required (`slop`, `first_person`); `spoken` = the `as-user` rules plus `Spoken.Markup`; `inputs.commit_message_unit(raw: str, *, cleanup: Literal["editor","none"]) -> Unit` (`editor` drops `#` comment lines and the scissors line with everything after it, as git's default cleanup; `none` keeps the message as committed; trailers are kept in both); CLI flag `--cleanup editor|none`, default `none`.

- [ ] **Step 1: Write the failing tests:** `test_spoken_flags_url_and_bold` (`"Открой **ссылку** https://x.y"` → two `Spoken.Markup` findings); `test_spoken_reads_raw_markdown_and_html` (through `check_units` with input@1: a `markdown` field ``"скажи `hello` https://example.com"`` → findings for the code span and the URL; an `html` field `"<b>важно</b>"` → a finding for the tag); `test_spoken_plain_passes`; `test_commit_cleanup_editor` (comment lines and the scissors section removed, line numbers still match the file); `test_commit_cleanup_none_keeps_scissors_content` (with `cleanup="none"` text below a scissors line is checked); `test_commit_trailer_attribution` (`"feat: add x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"` → `ai-tells-commits.CommitAttribution` under both cleanup modes); `test_commit_profile_flags_attribution` (`"feat: add x\n\nGenerated with Claude Code"` → an `ai-tells-commits.CommitAttribution` finding).
  In `tests/test_as_user.py` (fake Laya socket answering `first_person` 0.9 unless a test sets it): `test_as_user_emoji` (`"Готово ✅"` in `as-user` → `AsUser.Emoji`); `test_tg_bot_allows_emoji` (the same text in `tg-bot` → no emoji finding of any rule, `TextLint.DoneEmoji` included); `test_as_user_bot_formatting` (`"<b>Привет</b>"` in an html field and `"## Итоги\n- пункт"` in a markdown field → `AsUser.BotFormatting` for each); `test_as_user_not_first_person` (fake socket answers `first_person` 0.3 → `AsUser.NotFirstPerson`; 0.7 → none); `test_spoken_includes_as_user_rules` (an emoji in `spoken` → both `AsUser.Emoji` and `Spoken.Markup`).
- [ ] **Step 2: Run** `tests/test_native_spoken.py` and `tests/test_as_user.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the kinds, the cleanup, and the profiles per the spec table (§4.5).
- [ ] **Step 4: Run** the three files one by one. Expected: PASS.
- [ ] **Step 5: Commit** `feat: as-user, spoken, chat, commit and ui-strings profiles`.

### Task 7: Suppressions, `.text-lint.yaml`, `--enforce repo`

**Files:**
- Create: `text_lint/suppress.py`, `text_lint/config.py`, `tests/test_suppress.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Suppression` dataclass: `path: str | None, rules: list[str], line: int, scope: Literal["line","next-line","block"], end_line: int | None, reason: str | None`.
  - `suppress.parse_inline(text: str, *, path: str | None) -> list[Suppression]`.
  - `RepoConfig` dataclass: `exclude: list[str], disable: list[RuleReason], suppress: list[PathSuppression], ui_calls: dict[str, list[str]]`; `config.load_repo_config(root: Path, *, rev: str | None = None) -> RepoConfig` (raises `ConfigError` on a missing or short reason; `rev=None` reads the working tree; `rev=":"` reads the index with `git show :.text-lint.yaml`; any other `rev` reads `git show <rev>:.text-lint.yaml`; a file absent in that snapshot means the empty config, whatever the working tree holds).
  - `config.enforce_mode(root: Path) -> Literal["warn","block"]`.
  - `suppress.apply(findings, inline: list[Suppression], cfg: RepoConfig, sources: Mapping[str, list[str]]) -> tuple[list[Finding], list[dict], list[Finding]]` (kept, suppressed records, `TextLint.SuppressionWithoutReason` findings); an inline suppression applies only to findings with the same `path`; `sources` maps a path to its file lines so `line_match` tests the whole source line of the finding, and `key` entries compare against `Finding.key`.

- [ ] **Step 1: Write the failing tests:** `test_disable_next_line_with_reason` (finding hidden, listed in `suppressed` with the reason); `test_reason_too_short` (`-- ok` → suppression ignored, `TextLint.SuppressionWithoutReason` reported); `test_meta_rule_not_suppressible`; `test_block_form` (`<!-- text-lint-disable ai-tells -- quoting examples -->` … `<!-- text-lint-enable -->` hides only inside); `test_key_suppression_is_per_key` (the same offending value under `errors.network` and `errors.disk`, a `suppress` entry for `errors.network` → only the `errors.disk` finding remains); `test_line_match_reads_source_line` (`line_match: "Wi-Fi"` suppresses a finding whose match is another word on that line); `test_config_reason_required` (a `disable` entry without `reason` → `ConfigError`, CLI exit 2); `test_enforce_mode_reads_rig_yaml` (`ci.items.text-lint.variant: block` → `"block"`; no key → `"warn"`); `test_enforce_mode_invalid_rig_yaml` (Review Focus 5: invalid YAML → `"warn"` and one stderr line).
  Also `test_inline_suppression_is_per_file`: `a.md` and `b.md` both have a finding on line 10 and only `a.md` has `text-lint-disable-line` on it → the `b.md` finding remains.
- [ ] **Step 2: Run** `tests/test_suppress.py`. Expected: FAIL.
- [ ] **Step 3: Implement**; inputs from input@1 never get inline suppressions (spec §7.2).
- [ ] **Step 4: Run** both files. Expected: PASS.
- [ ] **Step 5: Commit** `feat: suppressions with required reasons and repo enforce mode`.

### Task 8: `text-lint diff` and UI-string extraction

**Files:**
- Create: `text_lint/extract/{__init__,diff,classify,code,locale}.py`, `text_lint/extract/rules/{tsx,typescript,javascript,python,swift,kotlin}.yml`, `tests/test_diff.py`, `tests/test_extract_code.py`, `tests/test_extract_locale.py`, `tests/fixtures/extract/` (one sample file per language)
- Modify: `text_lint/cli.py` (`diff`, `extract`), `pyproject.toml` (add `ast-grep-py>=0.51,<0.52`), `.github/workflows/tests.yml` (append `ast-grep-py>=0.51,<0.52` to `TEST_EXTRA_DEPS`)

**Interfaces:**
- Consumes: `Unit`, `check_units` (Task 2), `RepoConfig`, `enforce_mode`, `suppress.apply` (Task 7).
- Produces:
  - `diff.changed_lines(repo: Path, *, source: Literal["staged","worktree","base"], base: str | None, head: str | None = None) -> dict[str, set[int]]`, and `diff.snapshot(source, head) -> str | None` naming where file contents and `.text-lint.yaml` are read: `":"` (index) for `staged`, `None` (working tree) for `worktree`, `head or "HEAD"` for `base`; contents come from `git show <snapshot>:<path>` whenever the snapshot is not the working tree.
  - `classify.classify_path(path: str, cfg: RepoConfig) -> Literal["prose","code","locale","skip"]`.
  - `code.extract(path: str, source: str, lang: str, extra_calls: list[str]) -> list[Unit]` (one unit per string, with `path`, `raw` = the string's static text with `{}` for interpolations, a `raw_map` covering every character of `raw`, and `norm_to_raw`, so interpolations and multi-line strings keep source positions).
  - `locale.extract(path: str, source: str) -> list[Unit]` (`key`, `raw_map` and `norm_to_raw` set).
  - `diff.run(repo: Path, *, source: Literal["staged","worktree","base"], base: str | None, head: str | None, enforce: str) -> dict` (one merged report; one Vale and one aiw-ru call per profile group).

- [ ] **Step 1: Write the failing tests** (each builds a temporary git repo):
  - `test_only_changed_lines`: a Markdown file with an old slop line and a new one → one finding, on the new line.
  - `test_sources_differ`: one slop line staged and another only in the working tree → `--staged` reports the first only, `--worktree` the second only, `--base HEAD~1` neither until committed.
  - `test_base_head_reads_blobs`: with the base commit checked out and the PR commit only fetched (`git fetch` into a ref), `diff --base <base> --head <pr>` reports the PR's slop line, and an unrelated slop line in the working tree is not read.
  - `test_config_from_head_revision`: base has no `.text-lint.yaml`, head adds one with a reasoned `suppress` entry for its new offending string → `diff --base <base> --head <head>` reports it as suppressed; a second head that deletes a base `.text-lint.yaml` → the base's suppression no longer applies, even though the checked-out working tree still has the file.
  - `test_staged_reads_index_snapshot`: a staged README slop line plus an unstaged `.text-lint.yaml` suppressing it → `diff --staged` still reports it; a staged suppression later removed from the working tree → still applies; a staged line later edited away in the working tree → still reported.
  - `test_deleted_only_passes` (Review Focus 1): a commit that only deletes lines → verdict `pass`, exit 0.
  - `test_rename_with_edit` (Review Focus 3): `git mv` plus one edited line → only that line is checked.
  - `test_excluded_fixture`: a slop line under `tests/fixtures/` → no finding.
  - `test_tsx_extraction`: `<button title="Save it">Готово! ✅ {n} items</button>` yields units `"Save it"`, `"Готово! ✅ {} items"` with correct lines.
  - `test_python_extraction`: `print(f"Saved {n} files")` and `add_argument("--x", help="Do the thing")` yield `"Saved {} files"` and `"Do the thing"`; `"--x"` is skipped.
  - `test_position_after_interpolation`: `print(f"Saved {some_long_expression} files utilize")` → the `TextLint.Utilize` finding has the source column of `utilize`, not its column in `"Saved {} files utilize"`; a triple-quoted string spanning three lines with an edit on its third line → the string is checked and the finding has line 3.
  - `test_swift_kotlin_extraction`: `Text("Hello there")` in Swift and Kotlin each yield one unit.
  - `test_locale_formats`: JSON leaf values, YAML scalars, `.po` `msgstr`, WebExtension `message`, Hugo TOML `other = "…"` each yield units with the right lines and `key` set (`errors.network`, the msgid, `other`).
  - `test_engine_calls_per_profile`: with fake `vale` and `aiw-ru` that record invocations, ten changed Russian UI strings and one changed Russian README paragraph → exactly two Vale calls and two aiw-ru calls, the aiw-ru calls with `--context docs` (prose group) and `--context chat` (ui-strings group); ten UI strings alone → one call of each.
  - `test_staged_writes_hook_log`: with `XDG_CACHE_HOME` pointed at a temp dir, `diff --staged` with one finding appends one JSON line with `rule_id`, `repo`, `path`, `line`, `verdict` and no `match` or text.
- [ ] **Step 2: Run** `tests/test_extract_code.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `git diff -U0 --no-color -M --diff-filter=AMR`, the path table and default excludes from spec §6.1, the extraction rules from §6.2; group units by profile and call `check_units` once per group (which batches the engine calls, Task 2).
- [ ] **Step 4: Run** the three files one by one. Expected: PASS.
- [ ] **Step 5: Commit** `feat: text-lint diff with UI-string extraction on changed lines`.

### Task 9: Selftest, golden tests, budget and offline tests, docs, 0.4.0

**Files:**
- Create: `text_lint/selftest.py`, `tests/test_rule_examples.py`, `tests/test_golden.py`, `tests/golden/<profile>/…` (a clean case and one case per pack for each of the 9 profiles), `tests/test_budget.py`, `docs/adr/0002-text-lint-scope-and-contract.md`
- Modify: `text_lint/cli.py` (`rules`, `selftest`), `.github/workflows/tests.yml` (run `text-lint selftest`; the `wheel-smoke` step from Task 2 now runs `text-lint selftest` from the installed wheel outside the checkout; run the integration test under `unshare -rn` when available), `README.md`, `text_lint/install.py` (blurb `text-lint.md`), `CHANGELOG.md`

**Interfaces:**
- Produces: `selftest.run(rule_glob: str = "*") -> list[str]` (ids of rules that reported nothing on their own `flag` examples); `text-lint selftest` exits 1 when the list is non-empty and prints it.

- [ ] **Step 1: Write the failing tests:** `test_every_rule_has_positive_example` (all native rules and every `TextLint` Vale rule); `test_examples_behave` (each `flag` example yields its rule, each `pass` example does not); `test_golden[<profile>/<case>]` (compares `rule_id`, `line`, `col`, `severity`, `verdict`; `TEXT_LINT_UPDATE_GOLDEN=1` rewrites); `test_budget_tg_bot` and `test_budget_as_user` (with a warm fake Laya socket, p95 of 5 runs ≤ 1.5 s on `tests/golden/tg-bot/long-mixed.json` and `tests/golden/as-user/long-mixed.json`: Russian `title`, `subagent` and `harness_title` fields plus a 4096-character mixed `body`, the shape tg-cli sends); `test_budget_spoken` (native rules p95 ≤ 250 ms on 1000 characters); `test_tg_bot_fail_on_after_rollout` (a `tg-bot` input containing `utilize`, under `enforce="block"`: with the warn-phase profile values (Vale cap `warning`, `fail_on: error`) the verdict is `warn`; with the post-rollout values (no cap, `fail_on: warning`) it is `block`; the same pair for `as-user`); `test_selftest_reports_silent_rule` (a pack with a rule whose `flag` example does not match → listed).
- [ ] **Step 2: Run** `tests/test_rule_examples.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `selftest`, `rules`, the golden cases, ADR 0002 (records spec §3 and the decisions the owner approved), README sections for profiles, suppressions and exit codes; bump to `0.4.0`; tag `v0.4.0` after merge.
- [ ] **Step 4: Run** `tests/test_rule_examples.py` locally (PASS); the budget and golden tests run in PR CI.
- [ ] **Step 5: Commit** `feat: selftest, golden and budget tests; text-lint 0.4.0`.

## Repo: agent-tools

### Task 10: CI slot, hook fragments, skill

**Files:**
- Create: `ci/text-lint/workflow.yml` (same content as `workflow-warn.yml`), `ci/text-lint/workflow-warn.yml`, `ci/text-lint/workflow-block.yml`, `ci/text-lint/text-lint.sh`, `git-hooks/global-dispatcher/global-hooks.d/pre-commit/30-text-lint`, `git-hooks/global-dispatcher/global-hooks.d/commit-msg/30-text-lint`, `skills/universal/text-lint/SKILL.md`, `tests/test_text_lint_fragments.py`, `tests/test_text_lint_ci.py`
- Delete: `skills/universal/slop-check/` (pointer kept in CHANGELOG)
- Modify: `ci/README.md` (slot catalog), `skills/by-stack/frontend/ts/anti-slop/SKILL.md`:10, `README.md` inventory, `CHANGELOG.md`

**Interfaces:**
- Consumes: `text-lint diff --staged`, `text-lint check --profile commit --cleanup editor --enforce repo FILE` (hook), `text-lint check --profile commit --enforce <mode> FILE` (CI, stored message, no cleanup), `text-lint check --profile pr --laya-mode ci --input-json - --format json` (the runner sets `CI=true`), exit codes (Global Constraints).
- Produces: CI slot name `text-lint` with variants `warn` (`workflow-warn.yml`), `block` (`workflow-block.yml`), and `workflow.yml` = warn for a slot enabled without a variant (rig refuses a variant whose `workflow-<variant>.yml` is missing, `riglib/actions/runner.py`:688-692); fragment id `text-lint` (`# global-hook-id: text-lint`).

- [ ] **Step 1: Write the failing tests** with a fake `text-lint` on PATH that exits with `$FAKE_RC`: `test_precommit_missing_binary_warns` (no binary → exit 0, stderr has `text-lint not installed`); `test_precommit_rc` (rc 0 → 0, rc 1 → 1, rc 2 → 0 with the error printed); `test_skip_env` (`GLOBAL_HOOKS_SKIP=text-lint` → fragment not run). In `tests/test_text_lint_ci.py`, running `text-lint.sh` in a temporary repo with a base commit and two PR commits: `test_ci_checks_each_commit_message` (the fake receives one `check --profile commit` call per commit, with that commit's message); `test_ci_block_on_commit_message` (fake rc 1 on a message containing `Generated with Claude Code`, clean diff and PR body → script exits 1 under `TEXT_LINT_ENFORCE=block` and 0 under `warn`).
  Also `test_ci_trusts_base_only`: the PR head commit rewrites `ci/text-lint/text-lint.sh` to `exit 0`, sets `TEXT_LINT_ENFORCE: warn` in its copy of the workflow and `variant: warn` in `rig.yaml`, and adds a slop line; running the base branch's script (as `pull_request_target` does) with the base workflow's `TEXT_LINT_ENFORCE=block` still exits 1, the fake receives `diff --base <base> --head <pr> --enforce block`, and no file from the PR head is executed.
  Also `test_ci_pr_check_laya_mode_ci`: the fake receives `check --profile pr --laya-mode ci` with `CI=true` in its environment, and the job summary contains `laya: not run (ci runner)`.
  Also `test_commitmsg_uses_editor_cleanup`: the commit-msg fragment's fake receives `check --profile commit --cleanup editor --enforce repo <path>`, and the CI script's commit calls carry no `--cleanup` flag.
  Also `test_ci_commit_boundaries`: two PR commits with multi-line bodies, the first containing a `# ------------------------ >8 ------------------------` scissors line and the second an offending line → the fake receives exactly two `check --profile commit` calls, each with only its own commit's full message, and the second call reports the finding. `test_workflow_triggers`: each of the three workflow files parses as YAML, triggers on `pull_request_target` (not `pull_request`) with `opened`, `edited`, `synchronize`, `reopened`, `ready_for_review` under `types`, checks out the base ref, and fetches the PR head without checking it out.
- [ ] **Step 2: Run** `tests/test_text_lint_fragments.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the fragments (POSIX sh, no network), `text-lint.sh` (install uv, Python 3.12, `uv tool install git+https://git.hyperide.ai/ultrabricks/text-lint@${TEXT_LINT_REF}`, Vale `${VALE_VERSION}` with `${VALE_SHA256}`; `git fetch origin "$HEAD_SHA"` without checkout; then `text-lint diff --base "$BASE_SHA" --head "$HEAD_SHA" --enforce "$TEXT_LINT_ENFORCE"`; then, for each sha from `git rev-list --reverse "$BASE_SHA..$HEAD_SHA"`, `git log -1 --format=%B <sha>` into its own temp file and `text-lint check --profile commit --enforce "$TEXT_LINT_ENFORCE"` on that file; then the PR check built from `$GITHUB_EVENT_PATH` with the same `--enforce`, `--laya-mode ci` and `--format json`; then the job summary, including the line `laya: not run (ci runner)`; the script exits with the worst code, which is always 0 under `warn`). Everything the job executes comes from the base branch: `workflow-warn.yml` and `workflow.yml` set `TEXT_LINT_ENFORCE: warn`, `workflow-block.yml` sets `block`; all trigger on `pull_request_target` with `types: [opened, edited, synchronize, reopened, ready_for_review]`, check out the base ref with `fetch-depth: 0`, and take `BASE_SHA`/`HEAD_SHA` from the event payload. Write the skill from spec §4.3, §4.5 and §7.
- [ ] **Step 4: Run** the test file. Expected: PASS. Then install the slot in the text-lint repo with `variant: warn` and open a PR there with one slop line; the job summary lists it.
- [ ] **Step 5: Commit** `feat: text-lint CI slot and git hook fragments`.

### Task 11: Switch agent hooks to text-lint

Split by the hold on Task 12. Switching `chat-dictionary-gate` (Steps 1-4 for the gate) does not wait. Deleting `decision-request-format` relies on tg-cli enforcing the same rules, so that deletion (the `Delete:` line and the second half of the commit) waits for Task 12, which is **ON HOLD**; until then it lands as its own commit `refactor: drop decision-request-format hook` after Task 12 merges.

**Files:**
- Modify: `agent-hooks/chat-dictionary-gate/chat_dict_gate.py` (replace the matcher with a `text-lint check --profile chat --input-json - --format json` call), `tests/test_chat_dictionary_gate.py`
- Delete: `agent-hooks/decision-request-format/`, `~/.claude/hooks/decision-request-format.pre-bash.json` through rig's agent-hook removal

**Interfaces:**
- Consumes: `chat` profile, exit codes.

- [ ] **Step 1: Write the failing tests:** `test_blocks_on_rc1` (fake text-lint rc 1 with a report → hook exit 10 and the findings in the message); `test_spawns_json_output` (the fake's argv contains `--profile chat`, `--input-json -` and `--format json`); `test_fails_open_on_rc2_and_timeout`; `test_contract_real_binary` (skipped when `text-lint` is absent; a DICT fixture word → exit 10).
- [ ] **Step 2: Run** the test file. Expected: FAIL.
- [ ] **Step 3: Implement**: keep the hook's turn extraction and `only_if_cyrillic` judged over the whole turn by sending all text blocks as fields of one input@1 document; delete the regex code.
- [ ] **Step 4: Run** the test file. Expected: PASS.
- [ ] **Step 5: Commit** `refactor: chat dictionary gate calls text-lint` (the hook deletion follows Task 12, see above).

## Repo: tg-cli

### Task 12: Send gates call text-lint

**ON HOLD** (owner question tg#22710: tg-cli in Python). The content below is kept for when the answer comes; profile selection follows spec §12 R7.

**Files:**
- Create: `features/cli/text-lint.ts`, `tests/text-lint.test.ts`, `tests/text-lint-contract.test.ts`
- Modify: `tg` (replace the blocks at 1505, 1527-1549, 1580-1637, 1653-1656, 1708-1729 and `enforceEscalationGate` 144-157 / 1324 with one call), `features/cli/args.ts` (drop the escalation entry from `TAG_GATES`, the `escalationFormatGate` function at :272-295 and the `escalation-format` import at :15), `CHANGELOG.md`, `README.md`
- Delete: `features/cli/{dict-gate,cjk-guard,mixed-script-guard,escalation-format,message-structure,laya-socket}.ts`, the classifier and validators in `features/cli/intent-gate.ts` (the obligations wiring stays), and their tests

**Interfaces:**
- Consumes: input@1, report@1, exit codes.
- Produces: `runTextLint(input: TextLintInput, opts: { profile: "tg-bot" | "as-user" | "spoken"; ignore: string[]; env: NodeJS.ProcessEnv }): TextLintOutcome` where `TextLintOutcome = { kind: "absent" } | { kind: "error"; message: string } | { kind: "report"; report: TextLintReport }`, spawning `text-lint check --profile <opts.profile> --input-json - --format json` with `Bun.spawnSync`; `TextLintInput` fields may set `gate: false`.

- [ ] **Step 1: Write the failing tests:** `runTextLint` with a fake binary: absent → `{kind:"absent"}`; rc 2 → `{kind:"error"}` carrying stderr; rc 1 → report with verdict `block`; the spawned argv contains `--profile` and `opts.profile`. CLI tests: `tg --dry-run` with a DICT fixture word refuses with exit 1 and prints the rule id; `ESCALATION_GATE_ENFORCE=0` turns a `Format.Decision` finding into a warning and exit 0; `--no-feature cjk-guard` passes `--ignore Script.StrayCjk --ignore Script.MixedScript`; malformed DICT via `TG_DICT_PATH` in `~/.config/tg-cli/.env` refuses and names the file (Review Focus 2); a send without `--title` whose harness title carries a DICT word and whose body is clean is sent, with the harness title dropped from the header and the session label (as `tg`:1633-1645 today); a report with `intent.action == "follow-up"` and two `follow_ups` records two obligations, and a later reply whose report has `intent.valid == "decision"` settles exactly one of them. Contract test against the real binary (skipped when absent).
  Also: a plain `tg` send spawns `--profile tg-bot`, chosen in code with no CLI flag to change it; the argv never carries a Laya switch, and `TG_LAYA` set to `0` changes nothing; `TG_LAYA_SOCKET=/x` reaches the child as `TEXT_LINT_LAYA_SOCKET=/x`; rc 2 with a Laya message refuses the send and prints it.
  Also an entrypoint smoke test after the deletions: `tg --help` and `tg --dry-run "x"` exit 0 with no module-resolution error, and `grep -r "escalation-format\|dict-gate\|cjk-guard\|mixed-script-guard\|message-structure\|laya-socket" features tg` finds no import.
  Also, through tg-cli's real field construction: a compliant decision body containing `<b>` sent with `--tag decision` and no `--format html` → refused with `Format.Decision` (the body is detected as HTML at `tg`:1486, but `meta.send_format` is `null`), as `escalation-gate-args.test.ts`:73 asserts today.
- [ ] **Step 2: Run** `bun test tests/text-lint.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the call at the dictionary gate's position, before the `--dry-run` cut-off, with the fields and mappings of spec §5.1; delete the modules and their tests only after text-lint's parity tests for them are green on `main`.
- [ ] **Step 4: Run** `bun test tests/text-lint.test.ts` and `bun test tests/text-lint-contract.test.ts`. Expected: PASS; full suite in PR CI.
- [ ] **Step 5: Commit** `refactor!: send gates run through text-lint`.

### Task 13: Voice path

**ON HOLD** (owner question tg#22710: tg-cli in Python). The content below is kept for when the answer comes.

**Files:**
- Modify: `features/user-account/cli.ts` (check before spawning the sidecar), `features/telegram_client/conversation.py` (check each reply before `tts.synthesize`), `tests/user-account-text-lint.test.ts`, `features/telegram_client/tests/test_conversation_text_lint.py`

**Interfaces:**
- Consumes: `runTextLint` (Task 12), `as-user` and `spoken` profiles.

- [ ] **Step 1: Write the failing tests:** `tg call --say` with a DICT fixture word → exit 1, no sidecar spawned, and the fake binary received `--profile as-user`; `tg call --say "**жирный** текст"` → refused with `AsUser.BotFormatting` (error); `tg call --say "Готово ✅"` → refused with `AsUser.Emoji`; `tg user send` spawns `--profile as-user` with no `meta.tag`; a duplex reply spawns `--profile spoken`; duplex reply with findings is still synthesized and the findings are logged; `test_interrupt_while_linter_stalls` (a fake text-lint that sleeps 5 s; an interruption `event()` during the check is handled within 100 ms, the fake process is killed, and no audio for the cancelled reply is sent); `test_duplex_missing_binary` (no `text-lint` on PATH → the reply is synthesized, one log line, the call is not ended).
- [ ] **Step 2: Run** the two test files. Expected: FAIL.
- [ ] **Step 3: Implement** (duplex: `asyncio.create_subprocess_exec("text-lint", "check", "--profile", "spoken", "--input-json", "-", "--format", "json")` awaited with `asyncio.wait_for(..., timeout=2)` (room for the Laya request) inside `Conversation.respond()` (`features/telegram_client/conversation.py`:156), so the event loop keeps serving `event()` (:96-97); on timeout or task cancellation kill the process and await it; `OSError` at process start (binary missing), a timeout or rc 2 logs and continues to synthesis).
- [ ] **Step 4: Run** the two test files. Expected: PASS.
- [ ] **Step 5: Commit** `feat: spoken and user-sent text go through text-lint`.

## Repo: rig-cli

### Task 14: Register the slot and fragments, defaults, status

**Files:**
- Modify: `riglib/config_schema.py` and `schema/rig.schema.json` (regenerated) only if the known lists live there; `docs/config-schema.md` (known lists :513-585, `ci` :433-457, `git_hooks` :405-430), `riglib/drift.py` (report a repo without the `text-lint` slot), the default config rig writes for new repos, `CHANGELOG.md`
- Test: `tests/test_text_lint_slot.py`

**Interfaces:**
- Consumes: slot `text-lint` with `workflow-warn.yml`, `workflow-block.yml` and `workflow.yml` (Task 10); fragment id `text-lint`.

- [ ] **Step 1: Write the failing tests:** `test_apply_installs_warn_variant` (run the real `install_ci` action, not only the plan: `ci.items.text-lint: {enabled: true, variant: warn}` → `.github/workflows/text-lint.yml` equals the catalog's `workflow-warn.yml`); `test_apply_installs_block_variant` (`variant: block` → equals `workflow-block.yml`); `test_status_reports_missing_slot`; `test_fragment_opt_out` (`git_hooks.dispatcher.fragments.text-lint.enabled: false` → fragment not installed).
- [ ] **Step 2: Run** the test file. Expected: FAIL.
- [ ] **Step 3: Implement**; the global default `variant: warn` never changes (spec §8.1).
- [ ] **Step 4: Run** the test file. Expected: PASS.
- [ ] **Step 5: Commit** `feat: provision the text-lint CI slot and hook fragments`.

## Repo: task-cli

### Task 15a: `task classify-server start`

Runs before Task 5 (its on-demand Laya start calls this command).

**Files:**
- Modify: `tasklib/cli.py` (subcommand), `tasklib/classify_server.py` (a `start(wait_s: float = 20.0) -> Path` helper around `_spawn`), `README.md`, `CHANGELOG.md`
- Test: `tests/test_classify_server_start.py`

**Interfaces:**
- Produces: `task classify-server start`: spawns through `tasklib.classify_server._spawn`, waits until the socket accepts a connection (at most 20 s), prints the socket path, exit 0; exit 1 when it cannot start. Idempotent; the existing singleton flock makes concurrent starts safe.

- [ ] **Step 1: Write the failing tests** (a fake server module that binds a socket in a temp dir, no model): `test_start_when_absent` (socket absent → the command starts the server, prints the socket path, exit 0); `test_start_when_running` (a second call while running → exit 0 and no second spawn); `test_start_fails` (the spawn fails → exit 1 with the reason on stderr). In text-lint (`tests/test_laya.py`, Task 5) the matching end-to-end case: no server and the start command failing → `text-lint check` exits 2 naming Laya.
- [ ] **Step 2: Run** the test file. Expected: FAIL.
- [ ] **Step 3: Implement** on top of the existing spawn and flock (`tasklib/classify_server.py`:10-27); no systemd unit, the server keeps its idle exit (`IDLE_SECONDS=900`).
- [ ] **Step 4: Run** the test file. Expected: PASS.
- [ ] **Step 5: Commit** `feat: task classify-server start for on-demand Laya`.

### Task 15: `prose` gate on tickets

**Files:**
- Modify: `tasklib/policy.py` (gate `prose`, `--skip-prose REASON`), `tasklib/cli.py`, `tests/test_policy_prose.py`, `README.md`

**Interfaces:**
- Consumes: `issue` profile, input@1 with fields `title` and `body` (markdown).
- Produces: `prose_violation(ticket: Ticket, cfg: EnforceConfig) -> Violation | None`, warning-level until `enforce.prose: block`.

- [ ] **Step 1: Write the failing tests:** findings → a warning listing rule ids, ticket still created; the fake's argv contains `--profile issue`, `--input-json -` and `--format json`; `enforce.prose: block` → refusal; `--skip-prose "reason"` recorded; text-lint absent → no warning, no failure.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement** following `todo_shaped_violation` (`tasklib/policy.py`:961) for the warning path.
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Commit** `feat: prose gate runs text-lint on ticket text`.

## Repo: review-cli

### Task 16: text-lint findings in the review context

**Files:**
- Modify: the diff-mode prompt builder in `reviewlib/` (the function that assembles the diff prompt), `tests/test_text_lint_context.py`

**Interfaces:**
- Produces: `text_lint_block(repo: Path, *, source: Literal["staged","worktree","stdin"]) -> str | None` (None when the binary is absent, the diff has no findings, or `source == "stdin"`); it runs `text-lint diff --staged` when the review runs on `git diff --cached` (`reviewlib/cli.py`:268), `text-lint diff --worktree` for the working-tree review, and nothing for a piped diff (`diff_from_stdin`, `reviewlib/cli.py`:7006).

- [ ] **Step 1: Write the failing tests:** fake binary with two findings → the prompt contains a `text-lint findings` section with both rule ids and the "already reported, do not repeat" line; absent binary → prompt unchanged; with the real binary in a temporary repo, a staged-only slop line appears for `review diff --staged` and not for the working-tree review, and an unstaged-only line the other way round; `git show HEAD | review diff` with an unrelated slop line in the working tree → no text-lint block and the fake is never called.
- [ ] **Step 2: Run** the file. Expected: FAIL.
- [ ] **Step 3: Implement** (`text-lint diff --staged|--worktree --format json --enforce warn`, chosen by the review's own diff source).
- [ ] **Step 4: Run** the file. Expected: PASS.
- [ ] **Step 5: Commit** `feat: feed text-lint findings into diff reviews`.

## Rollout

### Task 17: Warn everywhere, then block repo by repo, then drop the shim

- [ ] **Step 1:** After Tasks 10 and 14 merge, run `rig apply commit` so every repo gets `30-text-lint` fragments and the `text-lint` workflow in `warn`. Check: a commit with a slop line in any repo prints findings and succeeds; a PR shows the job summary.
- [ ] **Step 2:** For 14 days, once a week: collect findings per rule from CI summaries and `~/.cache/text-lint/hook.jsonl`, sample 20 per noisy rule, and tune or disable rules whose samples are mostly false positives (one text-lint PR each, reason in `vale.ini` or the rule file).
- [ ] **Step 3:** One `rig.yaml` PR per repo setting `ci.items.text-lint.variant: block`, in this order: text-lint, agent-tools, rig-cli, tg-cli, task-cli, review-cli, then the rest; a repo with an open false-positive report waits.
- [ ] **Step 4:** Lift the warning cap on Vale and aiw-ru findings in `tg-bot`, `as-user` and `spoken` (owner decision 1A, tg#22710), set `fail_on: warning` in `tg-bot.yaml` and `as-user.yaml` in the same text-lint PR (without it a Vale `warning` such as `TextLint.Utilize` stays below `fail_on: error` and never blocks; `test_tg_bot_fail_on_after_rollout` in Task 9 pins both value sets), and set task-cli's `enforce.prose: block`.
- [ ] **Step 5:** When 30 days have passed since the rename and a search of `~/dev`, `~/xp`, `~/work` and agent-tools finds no `slop-check` caller, remove `text_lint/shim.py` and the `slop-check` script in a minor release.

## Order and parallelism

1. Task 1 → Task 2 (sequential; everything depends on them).
2. Task 3 (it creates `NativeRule`, `native.KINDS` and the native engine wiring), with Task 7 and Task 15a (task-cli repo) in parallel to it; then Tasks 4, 5 and 6 in parallel (each its own module and tests; 5 and 6 touch distinct profile files that Task 2 created; 5 and 6 need 15a merged).
3. Task 8 (needs 7) → Task 9.
4. Tasks 10, 11 (the chat-dictionary-gate switch), 15, 16 in parallel after Task 9 is tagged; Task 14 after 10.
5. **ON HOLD** (owner question tg#22710: tg-cli in Python): Task 12, then Task 13 and the `decision-request-format` deletion from Task 11.
6. Task 17 (its steps for tg-cli wait with Task 12).
