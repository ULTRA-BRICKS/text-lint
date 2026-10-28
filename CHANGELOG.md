# Changelog

## 0.4.0

- Export `check`, `InputDocument` and `TextLintError` for Python consumers.
- Return report@1 with input@1 fields, sender metadata and intent results.
- Recover the reviewed dictionary, script, message and Laya foundation.
- Add `as-user` and `spoken` rules and reject missing native packs.
- Reject missing mandatory Laya answers; automated sends report an unreachable engine.
- Add packaged rule selftests and public API contract tests.
- Preserve original API string offsets through BOM and newline normalization.
- Give native rules normalized markup while keeping original source spans and logical lines.
- Align API leading decoded BOM handling with the first input@1 field. A BOM-only first
  field is empty for checking; untagged native rules are skipped.
- Correct input@1 coordinates: after one leading BOM a zero-width native span is `[1, 1]`,
  column 2, instead of `[0, 0]`, column 1. Normalize CRLF and lone CR in document fields
  for engines and patterns while retaining caller-original spans.
- Respect Vale's CR/CRLF/LF coordinates and aiw-ru's LF-only fallback coordinates.
  Exclude the full CRLF terminator from line content and column bounds.
- Preserve `os.fspath` semantics for dictionary paths in explicit and default environments.
- Use `/tmp` for absent or empty explicit `TMPDIR`; report invalid temporary directories
  as engine errors. Close partial startup logs and reap cancelled startup processes.
- Reject duplicate Laya JSON keys, non-object answers, non-`noul` answer types, conflicting
  `choice`/`score` fields and invalid probabilities. Tagless numeric `noul` answers remain
  supported; missing mandatory answers retain the existing error policy.
- Accept a per-check `environment` snapshot without changing process settings. Names and
  values the operating system cannot take (empty or `=` names, NUL, lone surrogates) raise
  `TextLintError` before any engine, socket or subprocess starts.
- Export the canonical `sender_prose(body: str) -> str` helper for consumers.
- Reuse one source line index per unit and bound numeric HTML entity conversion.
- Split each Markdown grid line once. Dashes-only rows inside a grid still open the shorter
  grids of the TypeScript reference, so decision counts match it.
- Ship a `py.typed` marker and declare `__all__` on `text_lint` and `text_lint.api`, so
  strict `mypy` sees the typed `check` and `sender_prose`.
- The plain-text CLI still removes one leading BOM; the API and input@1 remove all of them.

Refs: text-lint#4; tg-cli#768 and tg-cli#764.
