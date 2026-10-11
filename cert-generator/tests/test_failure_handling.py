from app import services
from tests.conftest import make_payload


def _patch_renderer_to_fail_for(monkeypatch, bad_name):
    real = services.render_certificate

    def flaky(data):
        if data.recipient_name == bad_name:
            raise RuntimeError("renderer exploded")
        return real(data)

    monkeypatch.setattr(services, "render_certificate", flaky)


def test_one_generation_failure_does_not_stop_the_others(client, monkeypatch):
    _patch_renderer_to_fail_for(monkeypatch, "Boom")
    recipients = [
        {"name": "Before", "email": "before@example.com"},
        {"name": "Boom", "email": "boom@example.com"},
        {"name": "After", "email": "after@example.com"},
    ]
    job = client.post("/jobs", json=make_payload(recipients)).json()
    status = client.get(f"/jobs/{job['id']}").json()

    assert status["status"] == "completed_with_errors"
    assert (status["succeeded"], status["failed"], status["pending"]) == (2, 1, 0)
    failure = status["failures"][0]
    assert failure["recipient_name"] == "Boom"
    assert "renderer exploded" in failure["error"]

    by_name = {c["recipient_name"]: c for c in client.get(f"/jobs/{job['id']}/certificates").json()["items"]}
    assert by_name["Before"]["status"] == by_name["After"]["status"] == "success"
    assert by_name["Boom"]["status"] == "failed" and by_name["Boom"]["download_url"] is None


def test_job_where_every_generation_fails_is_marked_failed(client, monkeypatch):
    _patch_renderer_to_fail_for(monkeypatch, "Boom")
    job = client.post("/jobs", json=make_payload([{"name": "Boom", "email": "b@example.com"}])).json()
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "failed"


def test_unsupported_characters_fail_only_that_certificate(client):
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "आशा राव", "email": "asha.hi@example.com"},  # Devanagari: not in built-in PDF fonts
    ]
    status = client.get(f"/jobs/{client.post('/jobs', json=make_payload(recipients)).json()['id']}").json()
    assert (status["succeeded"], status["failed"]) == (1, 1)
    assert "Unsupported character" in status["failures"][0]["error"]
