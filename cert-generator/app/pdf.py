"""Certificate rendering. Pure function: data in, PDF bytes out (no DB, no disk),
which makes it easy to test and easy to swap for another library."""
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

DEFAULT_ACHIEVEMENT = "for successfully participating in"


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    event_name: str
    issuer: str
    issue_date: date
    achievement: str | None = None


def _ensure_encodable(*texts: str) -> None:
    """Built-in PDF fonts only cover Western (cp1252) characters. Fail loudly for the
    certificate instead of silently printing garbage. (Fix: register a Unicode TTF font.)"""
    for text in texts:
        try:
            text.encode("cp1252")
        except UnicodeEncodeError as exc:
            bad = text[exc.start]
            raise ValueError(f"Unsupported character {bad!r} for the certificate font") from exc


def _fit_font_size(text: str, font: str, start: int, max_width: float, minimum: int = 12) -> int:
    size = start
    while size > minimum and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(data: CertificateData) -> bytes:
    achievement = data.achievement or DEFAULT_ACHIEVEMENT
    _ensure_encodable(data.recipient_name, data.event_name, data.issuer, achievement)

    buf = BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setTitle(f"Certificate - {data.recipient_name}")
    c.setAuthor(data.issuer)

    navy, gold = colors.HexColor("#1f2a44"), colors.HexColor("#b8902f")
    cx, max_w = page_w / 2, page_w - 160

    # Double border
    c.setStrokeColor(gold)
    c.setLineWidth(4)
    c.rect(24, 24, page_w - 48, page_h - 48)
    c.setLineWidth(1)
    c.rect(34, 34, page_w - 68, page_h - 68)

    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 38)
    c.drawCentredString(cx, page_h - 120, "CERTIFICATE")
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, page_h - 148, "OF ACHIEVEMENT")

    c.setFont("Helvetica", 15)
    c.drawCentredString(cx, page_h - 205, "This certificate is proudly presented to")

    name_font = "Times-BoldItalic"
    name_size = _fit_font_size(data.recipient_name, name_font, 44, max_w)
    c.setFont(name_font, name_size)
    c.setFillColor(gold)
    c.drawCentredString(cx, page_h - 260, data.recipient_name)
    c.setStrokeColor(navy)
    c.setLineWidth(0.8)
    c.line(cx - max_w / 2, page_h - 272, cx + max_w / 2, page_h - 272)

    c.setFillColor(navy)
    c.setFont("Helvetica", 15)
    c.drawCentredString(cx, page_h - 308, achievement)
    ev_size = _fit_font_size(data.event_name, "Helvetica-Bold", 26, max_w)
    c.setFont("Helvetica-Bold", ev_size)
    c.drawCentredString(cx, page_h - 345, data.event_name)

    # Footer: date (left) and issuer (right)
    c.setLineWidth(0.8)
    for x, label, value in (
        (150, "Date", data.issue_date.strftime("%d %B %Y")),
        (page_w - 150, "Issued by", data.issuer),
    ):
        c.line(x - 90, 95, x + 90, 95)
        c.setFont("Helvetica", _fit_font_size(value, "Helvetica", 12, 180, minimum=7))
        c.drawCentredString(x, 100, value)
        c.setFont("Helvetica-Oblique", 10)
        c.drawCentredString(x, 80, label)

    c.showPage()
    c.save()
    return buf.getvalue()
