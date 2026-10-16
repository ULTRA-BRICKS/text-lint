# slop-cli agent instructions

See `README.md` for what the tool does, how to install it and the test command; this file
carries the rules every session in this repo follows.

## Workflow

- Ticket first: `task new` (task-cli; backend Forgejo `ultrabricks/slop-cli`, see `rig.yaml`).
- Worktree only: `rig worktree create <name>`; the primary checkout stays on `main`.
- TDD red-first: write the failing test, watch it fail for the right reason, then fix.
- Atomic conventional commits; bump `slop_cli.__version__` in a behavior-changing PR.
- PR → review gate (`review`, under `flock /tmp/disk-session-review.lock`) → green `tests`
  CI → merge with `gh ship <PR>`.
- Prose you write here (README, ADRs, PR bodies) goes through `slop-check` first.

## Rule packs are data

Rules live in `slop_cli/vale/styles/`. `ai-tells` and `ai-tells-commits` are vendored from
tbhb/vale-ai-tells v1.37.0: do not edit them, tune them in `slop_cli/vale/vale.ini` (each
disabled rule carries its reason there). Our own rules go into `SlopCheck/*.yml`, one rule
per file, each with a fixture line in `tests/fixtures/` that it must flag.

## Heavy work runs on Runpod

This workstation is shared by many agent sessions. Locally run one test file at most, under
`nice -n19 ionice -c3`; the full suite runs in PR CI or on a Runpod pod via `runpod-run`
(skill `runpod-offload`):

```bash
runpod-run -- uv run --no-project --with 'pytest>=8,<9' --with 'aiw-ru>=2.5,<3' pytest tests -q
nice -n19 ionice -c3 uv run --no-project --with 'pytest>=8,<9' pytest tests/test_<one>.py -q
```

Never start the Laya classify server or load its model from here; `--laya` only talks to a
server that is already running, and tests use a fake socket server.
