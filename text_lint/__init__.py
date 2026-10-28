"""text-lint — flag AI tells in English and Russian prose.

The CLI lives in text_lint.cli; the console entry points are text_lint.cli:main
(`text-lint`) and text_lint.shim:main (`slop-check`, the deprecated 0.1 name), see
pyproject.toml [project.scripts]. bin/text-lint and bin/slop-check are thin shims for a
symlink install path.
"""

__version__ = "0.4.0"

# Import the internal check module before binding the public function, so later
# imports of text_lint.check cannot replace this callable with the module.
from .api import InputDocument, TextLintError, check, sender_prose

__all__ = ["InputDocument", "TextLintError", "check", "sender_prose"]
