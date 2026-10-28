"""The built distribution carries its type information to an external strict consumer."""

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONSUMER = """\
from typing import Any, assert_type

import text_lint
from text_lint import InputDocument, TextLintError, check, sender_prose
from text_lint.api import check as api_check

document: InputDocument = {
    "schema": "text-lint/input@1",
    "fields": [{"name": "body", "text": "hello", "format": "plain"}],
}
assert_type(check(document, profile="tg-bot", environment={"HOME": "/h"}), dict[Any, Any])
assert_type(api_check("text", laya_mode="ci"), dict[Any, Any])
assert_type(sender_prose("<p>x</p>"), str)
assert_type(text_lint.__version__, str)
try:
    check("x")
except TextLintError as error:
    assert_type(error, TextLintError)
"""

# Each line breaks a declared signature; unmarked or Any-typed APIs would accept them.
MISUSE = """\
from text_lint import check, sender_prose

check(["not", "text"])
check("x", environment={"HOME": 1})
sender_prose(b"bytes")
"""


def run(argv, cwd, env):
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=300, check=False
    )


def test_sdist_wheel_typed_consumer(tmp_path):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "MYPYPATH", "PYTHONHOME"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(ROOT / name, source / name)
    shutil.copytree(
        ROOT / "text_lint",
        source / "text_lint",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    # `python -m build` builds the sdist first and the wheel from that sdist.
    dist = tmp_path / "dist"
    built = run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(dist),
            str(source),
        ],
        tmp_path,
        env,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    (sdist,) = dist.glob("*.tar.gz")
    (wheel,) = dist.glob("*.whl")
    with tarfile.open(sdist) as archive:
        assert any(n.endswith("/text_lint/py.typed") for n in archive.getnames())
    with zipfile.ZipFile(wheel) as archive:
        assert "text_lint/py.typed" in archive.namelist()

    # A clean environment holding only the built wheel: the checkout is nowhere on its path.
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    venv = tmp_path / "venv"
    created = run([sys.executable, "-m", "venv", str(venv)], tmp_path, env)
    assert created.returncode == 0, created.stdout + created.stderr
    python = str(venv / "bin" / "python")
    installed = run(
        [python, "-m", "pip", "install", "--no-index", "--no-deps", "-q", str(wheel)],
        tmp_path,
        env,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr

    def strict_mypy(name, text):
        (consumer / name).write_text(text)
        return run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--strict",
                "--python-executable",
                python,
                "--cache-dir",
                str(tmp_path / "mypy-cache"),
                name,
            ],
            consumer,
            env,
        )

    ok = strict_mypy("consumer.py", CONSUMER)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "Success: no issues found" in ok.stdout

    bad = strict_mypy("misuse.py", MISUSE)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    flagged = [
        line.split(":")[1] for line in bad.stdout.splitlines() if ": error:" in line
    ]
    assert flagged == ["3", "4", "5"], (
        bad.stdout
    )  # One error per misused call, nothing more.
