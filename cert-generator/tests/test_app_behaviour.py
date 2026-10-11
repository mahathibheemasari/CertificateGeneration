from datetime import timedelta

from app import services
from app.main import resume_unfinished_jobs
from tests.conftest import make_payload


def test_root_redirects_to_docs(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/docs"


def test_timestamps_are_timezone_aware_utc(client):
    job = client.post("/jobs", json=make_payload()).json()
    detail = client.get(f"/jobs/{job['id']}").json()
    for field in ("created_at", "started_at", "finished_at"):
        assert detail[field].endswith(("Z", "+00:00")), detail[field]


def test_unfinished_jobs_are_resumed_after_restart(client, monkeypatch):
    # Simulate a crash: the job was accepted but never processed.
    monkeypatch.setattr("app.api.process_job", lambda job_id: None)
    job = client.post("/jobs", json=make_payload()).json()
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "pending"

    resumed = resume_unfinished_jobs(run_in_thread=False)  # what the startup hook does

    assert resumed == [job["id"]]
    detail = client.get(f"/jobs/{job['id']}").json()
    assert detail["status"] == "completed" and detail["succeeded"] == 2


def test_resume_does_not_regenerate_finished_jobs(client):
    client.post("/jobs", json=make_payload())
    assert resume_unfinished_jobs(run_in_thread=False) == []
