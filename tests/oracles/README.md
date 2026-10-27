# Legacy oracle reproduction

These development tools require an explicit, clean external tg-cli checkout at
`7aa28824a1371f95fb34c2dfc699794e6d7d3946` and Bun 1.4.2 revision
`744846f844374847c902b5e7fd59b4342a51ef99` (Unicode 17.0, ICU 78.3).
Neither Bun nor the external checkout is a production dependency.

Set `TG_ORACLE` to that checkout and `BUN_ORACLE` to the pinned executable.
Set `CAPTURE_DIR` to an empty directory under the working root's `tmp/textlint`.
Use an isolated HOME, empty dictionary and explicit absent Laya socket as for tests.

```sh
python tests/oracles/capture_foundation.py --checkout "$TG_ORACLE" --bun "$BUN_ORACLE" --output "$CAPTURE_DIR/foundation.json"
python tests/oracles/capture_unicode.py --checkout "$TG_ORACLE" --bun "$BUN_ORACLE" --output "$CAPTURE_DIR/unicode_categories.py"
cmp tests/fixtures/foundation-source-oracle.json "$CAPTURE_DIR/foundation.json"
cmp text_lint/unicode_categories.py "$CAPTURE_DIR/unicode_categories.py"
```

The foundation harness reads only the pattern, flags and text inputs from the immutable
fixture. It calls the original `loadDictionary`, `checkDictionary` and
`findMixedScriptTokens` exports; it never sends expected matches to Bun.
The Unicode harness queries Bun's property tests and single-character casing candidates,
then builds ranges and fold components in Python. Bun checks each resulting fold group
with `/iu`. Every codepoint from zero through U+10FFFF, including surrogates, is queried.
The generated module retains its historical header for byte comparison; the commands above
replace the removed TypeScript command named in that header.

Outputs must be new files. Each gets a `.provenance.json` sidecar containing the pinned
source revision, Bun binary hash, output hash and hashes of the external tracked files.
Expected fixtures and runtime tables are never overwritten by these commands.

For reproduction tests, set `TEXT_LINT_ORACLE_CHECKOUT` and `TEXT_LINT_ORACLE_BUN` to those
same paths. Without both, only the two external reproduction tests skip; validation tests
still run. Production and ordinary test installation do not install Bun.
