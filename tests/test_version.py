import subprocess
import sys
from pathlib import Path

from text_lint import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_flag_prints_package_version():
    out = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "text-lint"), "--version"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == f"text-lint {__version__}"


def test_slop_check_entry_runs_the_shim():
    out = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "slop-check"), "--version"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == f"slop-check {__version__}"
    assert "slop-check is deprecated" in out.stderr
