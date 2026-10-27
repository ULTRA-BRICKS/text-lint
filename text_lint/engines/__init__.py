"""The engines text-lint wraps: Vale (vale.py) and aiw-ru (aiwru.py) run as subprocesses and
map their output to report.Finding; Laya (laya.py) is the local task-cli classify server.

This module holds what the subprocess engines share: the error type, engine discovery from
the environment and the run with a timeout.
"""

import os
import subprocess

ENGINE_TIMEOUT_S = 120

# What reading an engine's JSON raises when the output does not have the documented shape.
MALFORMED = (ValueError, KeyError, TypeError, AttributeError, IndexError)


class EngineError(Exception):
    """A needed engine failed or is missing; the CLI exits 2 with this message."""

    def __init__(self, engine, problem, install=None):
        self.engine = engine
        self.problem = problem
        message = f"{engine}: {problem}"
        if install:
            message += f"\n  install: {install}"
        super().__init__(message)


class EngineMissing(EngineError):
    """The engine is not installed: a profile that marks it optional reports it as skipped."""


def env_override(*names):
    """(name, value) of the first of ``names`` set in the environment, else (None, None).
    ``TEXT_LINT_*`` names come first; the ``SLOP_CHECK_*`` ones stay accepted until the
    slop-check shim is removed."""
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return name, value
    return None, None


def explicit_path(*names):
    """(set, path) for an engine path in the environment: used as is, never replaced by a
    PATH lookup, so ``path`` is None when the value is not an executable file."""
    name, value = env_override(*names)
    if name is None:
        return False, None
    return True, value if os.path.isfile(value) and os.access(value, os.X_OK) else None


def missing(engine, env_names, default_problem, install):
    name, value = env_override(*env_names)
    if name is not None:
        return EngineMissing(engine, f"{name}={value} is not an executable file", install)
    return EngineMissing(engine, default_problem, install)


def run(engine, argv, text):
    try:
        # errors="replace": a stray undecodable byte must not crash the run (exit 1 would
        # read as "findings"); the JSON parse then reports a broken engine instead.
        return subprocess.run(argv, input=text, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=ENGINE_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise EngineError(engine, f"no answer within {ENGINE_TIMEOUT_S}s") from None
    except OSError as exc:
        raise EngineError(engine, f"could not start {argv[0]}: {exc}") from None


def unexpected(exc, stdout):
    return f"unexpected output ({type(exc).__name__}: {exc}): {stdout[:200]!r}"


def string(value):
    """``value`` if it is a string; anything else is malformed engine output."""
    if not isinstance(value, str):
        raise TypeError(f"expected a string, got {value!r}")
    return value
