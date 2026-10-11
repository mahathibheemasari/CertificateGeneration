import io
import re
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services, storage
from app.database import get_db
from app.models import Certificate, CertStatus, Job, JobStatus
from app.schemas import CertificateList, CertificateOut, FailureOut, JobCreate, JobOut
from app.services import process_job

router = APIRouter(prefix="/jobs", tags=["certificate jobs"])

MAX_FAILURES_IN_JOB_DETAIL = 100
FINISHED = (JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED)


# ---------------------------------------------------------------- helpers
def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


def _cert_out(request: Request, cert: Certificate) -> CertificateOut:
    url = None
    if cert.status == CertStatus.SUCCESS:
        url = str(request.url_for("download_certificate", job_id=cert.job_id, certificate_id=cert.id))
    return CertificateOut(
        id=cert.id,
        row_index=cert.row_index,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        error=cert.error,
        download_url=url,
    )


def _job_out(request: Request, db: Session, job: Job) -> JobOut:
    counts = services.status_counts(db, job.id)
    total = sum(counts.values())
    done = counts[CertStatus.SUCCESS] + counts[CertStatus.FAILED]

    failures = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job.id, Certificate.status == CertStatus.FAILED)
        .order_by(Certificate.row_index)
        .limit(MAX_FAILURES_IN_JOB_DETAIL)
    ).all()

    return JobOut(
        id=job.id,
        event_name=job.event_name,
        issuer=job.issuer,
        issue_date=job.issue_date,
        status=job.status,
        total=total,
        succeeded=counts[CertStatus.SUCCESS],
        failed=counts[CertStatus.FAILED],
        pending=counts[CertStatus.PENDING],
        progress_percent=round(100 * done / total, 1) if total else 100.0,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        download_all_url=(
            str(request.url_for("download_all", job_id=job.id)) if counts[CertStatus.SUCCESS] else None
        ),
        failures=[
            FailureOut(
                certificate_id=c.id,
                row_index=c.row_index,
                recipient_name=c.recipient_name,
                recipient_email=c.recipient_email,
                error=c.error,
            )
            for c in failures
        ],
        failures_truncated=counts[CertStatus.FAILED] > MAX_FAILURES_IN_JOB_DETAIL,
    )


# ---------------------------------------------------------------- endpoints
@router.post("", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: JobCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Accept a bulk request. Responds immediately (202); certificates are generated in the background."""
    job = services.create_job(db, payload)
    if job.status == JobStatus.PENDING:  # at least one valid recipient
        background_tasks.add_task(process_job, job.id)
    return _job_out(request, db, job)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Status + progress + the list of failures (poll this)."""
    return _job_out(request, db, _get_job_or_404(db, job_id))


@router.get("/{job_id}/certificates", response_model=CertificateList)
def list_certificates(
    job_id: str,
    request: Request,
    status_filter: str | None = Query(None, alias="status", pattern="^(pending|success|failed)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Per-recipient results, filterable by status and paginated."""
    _get_job_or_404(db, job_id)
    base = select(Certificate).where(Certificate.job_id == job_id)
    if status_filter:
        base = base.where(Certificate.status == status_filter)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(Certificate.row_index).limit(limit).offset(offset)).all()
    return CertificateList(total=total, limit=limit, offset=offset, items=[_cert_out(request, c) for c in rows])


@router.get("/{job_id}/certificates/{certificate_id}/download", name="download_certificate")
def download_certificate(job_id: str, certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None or cert.job_id != job_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Certificate not found")
    if cert.status != CertStatus.SUCCESS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Certificate is not available (status: {cert.status})"
        )
    path = storage.path_for(cert.file_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Certificate file is missing from storage")
    return FileResponse(path, media_type="application/pdf", filename=f"certificate_{cert.row_index:04d}.pdf")


@router.get("/{job_id}/download", name="download_all")
def download_all(job_id: str, db: Session = Depends(get_db)):
    """All successfully generated certificates of the job as a single ZIP."""
    _get_job_or_404(db, job_id)
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertStatus.SUCCESS)
        .order_by(Certificate.row_index)
    ).all()
    if not certs:
        raise HTTPException(status.HTTP_409_CONFLICT, "No certificates have been generated yet")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            safe_name = re.sub(r"[^A-Za-z0-9]+", "_", cert.recipient_name).strip("_")[:40] or "recipient"
            zf.write(storage.path_for(cert.file_path), f"{cert.row_index:04d}_{safe_name}.pdf")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job_id}.zip"'},
    )
