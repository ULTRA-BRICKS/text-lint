"""slop-check command line (scaffold: --version only; the checker lands in its own PR)."""

import argparse

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="slop-check")
    parser.add_argument("--version", action="version", version=f"slop-check {__version__}")
    parser.parse_args(argv)
    return 0
