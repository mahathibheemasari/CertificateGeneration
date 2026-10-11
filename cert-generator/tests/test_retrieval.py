import io
import zipfile

from app import services
from tests.conftest import make_payload


def _mixed_job(client, monkeypatch=None):
    recipients = [
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "", "email": "bad@example.com"},  # invalid -> failed
        {"name": "Ravi Kumar", "email": "ravi@example.com"},
    ]
    return client.post("/jobs", json=make_payload(recipients)).json()


def test_download_single_certificate(client):
    job = _mixed_job(client)
    items = client.get(f"/jobs/{job['id']}/certificates?status=success").json()["items"]
    resp = client.get(items[0]["download_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_list_certificates_filter_and_pagination(client):
    job = _mixed_job(client)
    jid = job["id"]
    assert client.get(f"/jobs/{jid}/certificates").json()["total"] == 3
    failed = client.get(f"/jobs/{jid}/certificates?status=failed").json()
    assert failed["total"] == 1 and failed["items"][0]["row_index"] == 1
    page = client.get(f"/jobs/{jid}/certificates?limit=1&offset=2").json()
    assert page["total"] == 3 and [i["row_index"] for i in page["items"]] == [2]
    assert client.get(f"/jobs/{jid}/certificates?status=bogus").status_code == 422


def test_download_all_zip_contains_only_successful_certificates(client):
    job = _mixed_job(client)
    resp = client.get(f"/jobs/{job['id']}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert names == ["0000_Asha_Rao.pdf", "0002_Ravi_Kumar.pdf"]
        assert all(zf.read(n).startswith(b"%PDF") for n in names)


def test_job_detail_links_to_zip(client):
    job = _mixed_job(client)
    # POST returns the job as accepted (before generation); the link appears once certificates exist.
    detail = client.get(f"/jobs/{job['id']}").json()
    assert client.get(detail["download_all_url"]).status_code == 200


def test_failed_certificate_cannot_be_downloaded(client):
    job = _mixed_job(client)
    failed = client.get(f"/jobs/{job['id']}/certificates?status=failed").json()["items"][0]
    resp = client.get(f"/jobs/{job['id']}/certificates/{failed['id']}/download")
    assert resp.status_code == 409


def test_pending_certificate_cannot_be_downloaded_and_zip_is_empty(client, monkeypatch):
    monkeypatch.setattr("app.api.process_job", lambda job_id: None)
    job = client.post("/jobs", json=make_payload()).json()
    cert = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]
    assert client.get(f"/jobs/{job['id']}/certificates/{cert['id']}/download").status_code == 409
    assert client.get(f"/jobs/{job['id']}/download").status_code == 409


def test_unknown_ids_return_404(client):
    job = _mixed_job(client)
    assert client.get(f"/jobs/{job['id']}/certificates/nope/download").status_code == 404
    assert client.get("/jobs/nope/download").status_code == 404
    assert client.get("/jobs/nope/certificates").status_code == 404


def test_certificate_cannot_be_fetched_through_another_job(client):
    job_a = _mixed_job(client)
    job_b = _mixed_job(client)
    cert_a = client.get(f"/jobs/{job_a['id']}/certificates?status=success").json()["items"][0]
    assert client.get(f"/jobs/{job_b['id']}/certificates/{cert_a['id']}/download").status_code == 404
