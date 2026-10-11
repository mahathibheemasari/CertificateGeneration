"""File storage for generated PDFs. Local disk here; the interface (save/path_for)
is small so S3 or similar could replace it without touching the rest of the app."""
from pathlib import Path

from app import config


def save_pdf(job_id: str, certificate_id: str, content: bytes) -> str:
    """Write the PDF and return its path relative to STORAGE_DIR."""
    relative = Path(job_id) / f"{certificate_id}.pdf"
    target = config.STORAGE_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(content)
    tmp.replace(target)  # atomic: never leave a half-written file behind
    return relative.as_posix()


def path_for(relative: str) -> Path:
    return config.STORAGE_DIR / relative
