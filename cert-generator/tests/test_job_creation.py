from tests.conftest import make_payload


def test_create_job_returns_202_with_job_details(client):
    resp = client.post("/jobs", json=make_payload())
    assert resp.status_code == 202
    body = resp.json()
    assert body["id"]
    assert body["total"] == 2
    assert body["event_name"] == "Python Bootcamp 2026"


def test_job_is_fully_generated_for_valid_input(client):
    job = client.post("/jobs", json=make_payload()).json()
    status = client.get(f"/jobs/{job['id']}").json()
    assert status["status"] == "completed"
    assert (status["succeeded"], status["failed"], status["pending"]) == (2, 0, 0)
    assert status["progress_percent"] == 100.0
    assert status["failures"] == []


def test_issue_date_defaults_to_today(client):
    payload = make_payload()
    del payload["issue_date"]
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 202
    assert resp.json()["issue_date"]
