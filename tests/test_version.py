import subprocess
import sys
from pathlib import Path

from slop_cli import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_flag_prints_package_version():
    out = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "slop-check"), "--version"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == f"slop-check {__version__}"
