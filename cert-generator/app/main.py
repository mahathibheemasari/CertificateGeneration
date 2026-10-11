import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import api, database, models, services  # noqa: F401  (models import registers the tables)
from app.database import Base, engine
from app.models import Job, JobStatus


def resume_unfinished_jobs(run_in_thread: bool = True) -> list[str]:
    """After a restart, jobs left 'pending'/'processing' would be stuck forever.
    process_job only touches pending certificates, so re-running it is safe (no duplicates)."""
    with database.SessionLocal() as db:
        job_ids = list(
            db.scalars(
                select(Job.id)
                .where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
                .order_by(Job.created_at)
            )
        )

    def run():
        for job_id in job_ids:
            services.process_job(job_id)

    if job_ids:
        if run_in_thread:
            threading.Thread(target=run, daemon=True, name="resume-jobs").start()
        else:
            run()
    return job_ids


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # fine for this scope; use Alembic migrations in production
    resume_unfinished_jobs()
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Submit a list of recipients, poll the job, download PDF certificates.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(api.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
