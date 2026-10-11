import pytest

from tests.conftest import make_payload


# ----- request-level problems -> the whole request is rejected with 422
@pytest.mark.parametrize(
    "mutation",
    [
        {"event_name": ""},
        {"issuer": ""},
        {"recipients": []},
        {"recipients": "not-a-list"},
        {"issue_date": "not-a-date"},
    ],
)
def test_invalid_request_is_rejected(client, mutation):
    assert client.post("/jobs", json=make_payload(**mutation)).status_code == 422


def test_missing_event_name_is_rejected(client):
    payload = make_payload()
    del payload["event_name"]
    assert client.post("/jobs", json=payload).status_code == 422


def test_too_many_recipients_is_rejected(client):
    many = [{"name": f"P{i}", "email": f"p{i}@example.com"} for i in range(51)]  # limit is 50 in tests
    assert client.post("/jobs", json=make_payload(many)).status_code == 422


# ----- row-level problems -> only that row fails
def test_invalid_rows_fail_but_valid_rows_are_still_generated(client):
    recipients = [
        {"name": "Good One", "email": "good@example.com"},
        {"name": "", "email": "noname@example.com"},
        {"name": "Bad Email", "email": "not-an-email"},
        {"name": "No Email"},
        "just a string",
        {"name": "Good Two", "email": "good2@example.com"},
    ]
    job = client.post("/jobs", json=make_payload(recipients)).json()
    status = client.get(f"/jobs/{job['id']}").json()

    assert status["status"] == "completed_with_errors"
    assert (status["total"], status["succeeded"], status["failed"]) == (6, 2, 4)
    failed_rows = {f["row_index"]: f["error"] for f in status["failures"]}
    assert set(failed_rows) == {1, 2, 3, 4}
    assert all("Validation failed" in err for err in failed_rows.values())
    assert "email" in failed_rows[2]


def test_duplicate_email_in_same_job_is_flagged(client):
    recipients = [
        {"name": "First", "email": "same@example.com"},
        {"name": "Second", "email": "SAME@example.com"},
    ]
    status = client.get(f"/jobs/{client.post('/jobs', json=make_payload(recipients)).json()['id']}").json()
    assert (status["succeeded"], status["failed"]) == (1, 1)
    assert "duplicate of row 0" in status["failures"][0]["error"]


def test_name_longer_than_limit_is_a_row_failure(client):
    recipients = [{"name": "x" * 101, "email": "long@example.com"}]
    job = client.post("/jobs", json=make_payload(recipients)).json()
    assert job["status"] == "failed"
    assert "name" in job["failures"][0]["error"]


def test_all_rows_invalid_finishes_immediately_as_failed(client):
    job = client.post("/jobs", json=make_payload([{"name": "", "email": "x"}])).json()
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert job["download_all_url"] is None
