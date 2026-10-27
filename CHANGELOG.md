# Changelog

## 0.4.0

- Export `check`, `InputDocument` and `TextLintError` for Python consumers.
- Return report@1 with input@1 fields, sender metadata and intent results.
- Recover the reviewed dictionary, script, message and Laya foundation.
- Add `as-user` and `spoken` rules and reject missing native packs.
- Reject missing mandatory Laya answers; automated sends report an unreachable engine.
- Add packaged rule selftests and public API contract tests.

Refs: text-lint#4; tg-cli#768 and tg-cli#764.
