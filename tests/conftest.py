import sys
from pathlib import Path

# Hermetic: import the checkout's slop_cli, never an installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
