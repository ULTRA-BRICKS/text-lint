# text-lint agent instructions

See `README.md` for what the tool does, how to install it and the test command; this file
carries the rules every session in this repo follows.

## Workflow

- Ticket first: `task new` (task-cli; backend Forgejo `ultrabricks/text-lint`, see `rig.yaml`).
- Worktree only: `rig worktree create <name>`; the primary checkout stays on `main`.
- TDD red-first: write the failing test, watch it fail for the right reason, then fix.
- Atomic conventional commits; bump `text_lint.__version__` in a behavior-changing PR.
- PR → review gate (`review`, under `flock /tmp/disk-session-review.lock`) → green `tests`
  CI → merge with `gh ship <PR>`.
- Prose you write here (README, ADRs, PR bodies) goes through `text-lint check` first.

## Rule packs are data

Rules live in `text_lint/packs/vale/styles/`. `ai-tells` and `ai-tells-commits` are vendored
from tbhb/vale-ai-tells v1.37.0: do not edit them, tune them in `text_lint/packs/vale/vale.ini`
(each disabled rule carries its reason there). Our own rules go into `TextLint/*.yml`, one rule
per file, each with a sample line in `text_lint/packs/vale/samples.tsv` that it must flag.

## Heavy work runs on Runpod

This workstation is shared by many agent sessions. Locally run one test file at most, under
`nice -n19 ionice -c3`; the full suite runs in PR CI or on a Runpod pod via `runpod-run`
(skill `runpod-offload`):

```bash
runpod-run -- uv run --no-project --with 'pytest>=8,<9' --with 'aiw-ru>=2.5,<3' --with 'PyYAML>=6,<7' --with 'jsonschema>=4,<5' --with 'mypy>=1,<3' --with 'build>=1,<2' --with 'setuptools>=77' pytest tests -q
nice -n19 ionice -c3 uv run --no-project --with 'pytest>=8,<9' --with 'PyYAML>=6,<7' --with 'jsonschema>=4,<5' --with 'mypy>=1,<3' --with 'build>=1,<2' --with 'setuptools>=77' pytest tests/test_<one>.py -q
```

Never start the Laya classify server, load its model or run the real `task classify-server`
from here. Every test gets a fake Laya socket (`TEXT_LINT_LAYA_SOCKET`) and a fake `task` first
on PATH from `tests/conftest.py`.
