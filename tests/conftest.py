import sys
from pathlib import Path

import pytest

# Hermetic: import the checkout's text_lint, never an installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKES = Path(__file__).resolve().parent / "fakes"


@pytest.fixture
def fake_engines(monkeypatch, tmp_path):
    """Fake vale and aiw-ru (tests/fakes) logging their calls under ``tmp_path``, which is
    returned. Not autouse: tests/test_integration.py runs the real engines."""
    monkeypatch.setenv("SLOP_CHECK_VALE", str(FAKES / "vale"))
    monkeypatch.setenv("SLOP_CHECK_AIWRU", str(FAKES / "aiw-ru"))
    monkeypatch.setenv("FAKE_VALE_LOG", str(tmp_path / "vale.log"))
    monkeypatch.setenv("FAKE_AIWRU_LOG", str(tmp_path / "aiwru.log"))
    # No real Laya socket can be reached from a test.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path
