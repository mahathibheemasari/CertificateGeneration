import os
import shutil
import tempfile

# Must be set BEFORE the app is imported, so tests never touch the real DB/storage.
_TMP = tempfile.mkdtemp(prefix="certtests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["STORAGE_DIR"] = f"{_TMP}/storage"
os.environ["MAX_RECIPIENTS_PER_JOB"] = "50"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(config.STORAGE_DIR, ignore_errors=True)
    yield


@pytest.fixture
def client():
    # NB: TestClient runs FastAPI background tasks before returning the response,
    # so after POST /jobs the job is already finished. Tests that need an in-flight
    # job disable the background task (see test_progress.py).
    with TestClient(app) as c:
        yield c


def make_payload(recipients=None, **overrides):
    payload = {
        "event_name": "Python Bootcamp 2026",
        "issuer": "Tech Academy",
        "issue_date": "2026-10-01",
        "recipients": recipients
        if recipients is not None
        else [
            {"name": "Asha Rao", "email": "asha@example.com"},
            {"name": "Ravi Kumar", "email": "ravi@example.com", "achievement": "for outstanding performance in"},
        ],
    }
    payload.update(overrides)
    return payload
