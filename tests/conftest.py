import shutil
from pathlib import Path

import pytest

from follow_audit import db

FIXTURES = Path(__file__).parent / "fixtures" / "exports"


@pytest.fixture
def make_data_dir(tmp_path):
    """Copy chosen fixture sets into a fresh data dir."""

    def _make(*names: str) -> Path:
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        for name in names:
            shutil.copytree(FIXTURES / name, data / name, dirs_exist_ok=True)
        return data

    return _make


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state" / "t.db"
    db.init_db(path)
    c = db.connect(path)
    yield c
    c.close()
