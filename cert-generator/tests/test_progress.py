from app import services
from tests.conftest import make_payload


def test_job_is_pending_until_processed_then_completes(client, monkeypatch):
    # Simulate "worker hasn't picked the job up yet" by disabling the background task.
    monkeypatch.setattr("app.api.process_job", lambda job_id: None)
    job = client.post("/jobs", json=make_payload()).json()

    pending = client.get(f"/jobs/{job['id']}").json()
    assert pending["status"] == "pending"
    assert (pending["succeeded"], pending["failed"], pending["pending"]) == (0, 0, 2)
    assert pending["progress_percent"] == 0.0
    assert pending["download_all_url"] is None

    services.process_job(job["id"])  # the worker runs

    done = client.get(f"/jobs/{job['id']}").json()
    assert done["status"] == "completed"
    assert done["progress_percent"] == 100.0
    assert done["started_at"] and done["finished_at"]


def test_progress_is_visible_mid_job(client, monkeypatch):
    """After the first certificate, a status request must show partial progress."""
    snapshots = []
    real_render = services.render_certificate
    job_id_holder = {}

    def spying_render(data):
        # Called once per certificate, BEFORE that certificate is committed.
        snapshots.append(client.get(f"/jobs/{job_id_holder['id']}").json())
        return real_render(data)

    monkeypatch.setattr("app.api.process_job", lambda job_id: job_id_holder.update(id=job_id))
    job = client.post("/jobs", json=make_payload()).json()
    job_id_holder["id"] = job["id"]
    monkeypatch.setattr(services, "render_certificate", spying_render)

    services.process_job(job["id"])

    assert [(s["status"], s["succeeded"]) for s in snapshots] == [("processing", 0), ("processing", 1)]
    assert snapshots[1]["progress_percent"] == 50.0


def test_unknown_job_returns_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_processing_a_finished_job_again_is_a_noop(client):
    job = client.post("/jobs", json=make_payload()).json()
    before = client.get(f"/jobs/{job['id']}").json()
    services.process_job(job["id"])
    assert client.get(f"/jobs/{job['id']}").json() == before
