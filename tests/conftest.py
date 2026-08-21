import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("JAFET_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("LIVE_BOOKING", raising=False)
