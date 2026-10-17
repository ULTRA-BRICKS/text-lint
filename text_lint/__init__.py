"""text-lint — flag AI tells in English and Russian prose.

The CLI lives in text_lint.cli; the console entry points are text_lint.cli:main
(`text-lint`) and text_lint.shim:main (`slop-check`, the deprecated 0.1 name), see
pyproject.toml [project.scripts]. bin/text-lint and bin/slop-check are thin shims for a
symlink install path.
"""

__version__ = "0.2.0"
