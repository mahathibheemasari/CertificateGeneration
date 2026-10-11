from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.pdf import CertificateData, render_certificate


def _text(pdf_bytes: bytes) -> str:
    return PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text()


def make_data(**kw):
    base = dict(
        recipient_name="Asha Rao", event_name="Python Bootcamp", issuer="Tech Academy", issue_date=date(2026, 10, 1)
    )
    base.update(kw)
    return CertificateData(**base)


def test_render_produces_a_pdf_with_recipient_specific_content():
    pdf = render_certificate(make_data())
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    for expected in ("Asha Rao", "Python Bootcamp", "Tech Academy", "01 October 2026"):
        assert expected in text


def test_custom_achievement_text_is_used():
    text = _text(render_certificate(make_data(achievement="for outstanding performance in")))
    assert "outstanding performance" in text


def test_very_long_name_still_renders():
    assert render_certificate(make_data(recipient_name="A" * 100)).startswith(b"%PDF")


def test_unsupported_characters_raise_a_clear_error():
    with pytest.raises(ValueError, match="Unsupported character"):
        render_certificate(make_data(recipient_name="Ashā 阿"))


def test_job_generates_one_distinct_pdf_per_recipient(client):
    job = client.post(
        "/jobs",
        json={
            "event_name": "Course X",
            "issuer": "Org",
            "recipients": [{"name": "Alice A", "email": "a@example.com"}, {"name": "Bob B", "email": "b@example.com"}],
        },
    ).json()
    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    texts = []
    for item in items:
        pdf = client.get(item["download_url"]).content
        texts.append(_text(pdf))
    assert "Alice A" in texts[0] and "Bob B" not in texts[0]
    assert "Bob B" in texts[1] and "Alice A" not in texts[1]
