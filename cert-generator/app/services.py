"""Business logic: validating recipients, creating jobs, and generating certificates."""
import logging
from datetime import date, datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import database, storage
from app.models import Certificate, CertStatus, Job, JobStatus
from app.pdf import CertificateData, render_certificate
from app.schemas import JobCreate, RecipientIn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- validation
def _format_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'recipient'}: {e['msg']}" for e in exc.errors()
    )


def validate_recipients(raw_items: list) -> list[tuple[int, RecipientIn | None, dict, str | None]]:
    """Validate every row independently.

    Returns (row_index, parsed_recipient_or_None, raw_dict, error_or_None) per row, so
    valid rows can proceed and invalid ones are reported (not silently dropped).
    """
    results = []
    seen_emails: dict[str, int] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            results.append((index, None, {}, "recipient must be an object with name and email"))
            continue
        try:
            recipient = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            results.append((index, None, raw, _format_error(exc)))
            continue
        key = recipient.email.lower()
        if key in seen_emails:
            results.append((index, None, raw, f"email: duplicate of row {seen_emails[key]}"))
            continue
        seen_emails[key] = index
        results.append((index, recipient, raw, None))
    return results


# ---------------------------------------------------------------- job creation
def _safe_str(value, limit: int = 255) -> str | None:
    return str(value)[:limit] if value is not None else None


def create_job(db: Session, payload: JobCreate) -> Job:
    """Persist the job and one Certificate row per input row, in a single transaction."""
    job = Job(
        event_name=payload.event_name,
        issuer=payload.issuer,
        issue_date=payload.issue_date or date.today(),
    )
    valid_count = 0
    for index, recipient, raw, error in validate_recipients(payload.recipients):
        if recipient is not None:
            valid_count += 1
            cert = Certificate(
                row_index=index,
                recipient_name=recipient.name,
                recipient_email=str(recipient.email),
                achievement=recipient.achievement,
                status=CertStatus.PENDING,
            )
        else:
            cert = Certificate(
                row_index=index,
                recipient_name=_safe_str(raw.get("name")),
                recipient_email=_safe_str(raw.get("email")),
                status=CertStatus.FAILED,
                error=f"Validation failed - {error}",
            )
        job.certificates.append(cert)

    if valid_count == 0:  # nothing to generate -> finished immediately
        now = datetime.now(timezone.utc)
        job.status, job.started_at, job.finished_at = JobStatus.FAILED, now, now
    db.add(job)
    db.commit()
    return job


# ---------------------------------------------------------------- progress
def status_counts(db: Session, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    counts = {CertStatus.PENDING: 0, CertStatus.SUCCESS: 0, CertStatus.FAILED: 0}
    counts.update({status: n for status, n in rows})
    return counts


def _final_status(succeeded: int, failed: int) -> str:
    if failed == 0:
        return JobStatus.COMPLETED
    if succeeded == 0:
        return JobStatus.FAILED
    return JobStatus.COMPLETED_WITH_ERRORS


# ---------------------------------------------------------------- generation
def process_job(job_id: str) -> None:
    """Generate all pending certificates of a job (runs in the background).

    * Each certificate is isolated in its own try/except and committed individually, so one
      failure never blocks the rest and progress is visible while the job runs.
    * It only touches PENDING rows, which makes it safe to re-run (idempotent resume).
    """
    with database.SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None or job.status not in (JobStatus.PENDING, JobStatus.PROCESSING):
            return
        try:
            job.status = JobStatus.PROCESSING
            job.started_at = job.started_at or datetime.now(timezone.utc)
            db.commit()

            pending = db.scalars(
                select(Certificate)
                .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING)
                .order_by(Certificate.row_index)
            ).all()

            for cert in pending:
                try:
                    pdf_bytes = render_certificate(
                        CertificateData(
                            recipient_name=cert.recipient_name,
                            event_name=job.event_name,
                            issuer=job.issuer,
                            issue_date=job.issue_date,
                            achievement=cert.achievement,
                        )
                    )
                    cert.file_path = storage.save_pdf(job_id, cert.id, pdf_bytes)
                    cert.status = CertStatus.SUCCESS
                    cert.error = None
                    cert.generated_at = datetime.now(timezone.utc)
                except Exception as exc:  # noqa: BLE001 - isolate ANY per-certificate failure
                    logger.exception("Certificate %s (job %s) failed", cert.id, job_id)
                    cert.status = CertStatus.FAILED
                    cert.error = f"Generation failed - {exc}"[:1000]
                db.commit()

            counts = status_counts(db, job_id)
            job.status = _final_status(counts[CertStatus.SUCCESS], counts[CertStatus.FAILED])
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:  # noqa: BLE001 - infrastructure problem (e.g. DB down)
            logger.exception("Job %s crashed", job_id)
            db.rollback()
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
