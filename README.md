# Bulk Certificate Generator

A backend API that accepts **one request containing many recipients**, generates a PDF certificate for each
valid recipient in the background, lets the client **track progress**, and lets it **download** the results
(individually or as a ZIP).

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default – any SQL database works via `DATABASE_URL`),
ReportLab (PDF), pytest.

---

## 1. Setup

Requires Python 3.10+. Open a terminal **inside the project folder** (the one containing `requirements.txt`).

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # (requirements.txt = runtime only)
```

**Windows (PowerShell)**
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```
If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope Process RemoteSigned` once and retry.

## 2. Run

```bash
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 – it redirects to the interactive docs (Swagger UI) at http://127.0.0.1:8000/docs,
where you can submit a job and download certificates without any extra tools.

Tables are created automatically on startup. Configuration (environment variables, all optional):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL (e.g. `postgresql+psycopg://...`) |
| `STORAGE_DIR` | `./storage` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `5000` | Upper bound per request |

## 3. Run the tests

```bash
python -m pytest
```

Tests use a throw-away SQLite DB and storage directory, so they never touch real data.

---

## 4. API usage

### Submit a bulk request — `POST /jobs`

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Request body:

| Field | Required | Notes |
|---|---|---|
| `event_name` | yes | ≤150 chars, printed on the certificate |
| `issuer` | yes | ≤150 chars |
| `issue_date` | no | `YYYY-MM-DD`, defaults to today |
| `recipients[]` | yes | 1 – `MAX_RECIPIENTS_PER_JOB` items |
| `recipients[].name` | yes | 1–100 chars |
| `recipients[].email` | yes | valid email, unique within the job (case-insensitive) |
| `recipients[].achievement` | no | ≤150 chars; replaces the default line "for successfully participating in" |

Response: **`202 Accepted`** with the job (including its `id`). Generation continues in the background.

### Check status / progress — `GET /jobs/{job_id}`

```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```

```json
{
  "id": "…", "status": "completed_with_errors",
  "total": 3, "succeeded": 2, "failed": 1, "pending": 0, "progress_percent": 100.0,
  "download_all_url": "http://127.0.0.1:8000/jobs/<job_id>/download",
  "failures": [
    {"certificate_id": "…", "row_index": 2, "recipient_name": "", "recipient_email": "invalid-row@example.com",
     "error": "Validation failed - name: String should have at least 1 character"}
  ]
}
```

Job `status` values:

| Status | Meaning |
|---|---|
| `pending` | accepted, worker hasn't started |
| `processing` | being generated (`progress_percent` rises) |
| `completed` | every certificate succeeded |
| `completed_with_errors` | some succeeded, some failed (see `failures`) |
| `failed` | nothing succeeded |

`failures` shows up to 100 entries; use the endpoint below for the complete list.

### Per-recipient results — `GET /jobs/{job_id}/certificates`

Query params: `status=pending|success|failed`, `limit` (default 100, max 1000), `offset`.
Each item has `row_index` (position in your request), `status`, `error`, and – when successful – a `download_url`.

### Retrieve certificates

```bash
# one certificate (PDF)
curl -o cert.pdf http://127.0.0.1:8000/jobs/<job_id>/certificates/<certificate_id>/download

# all successful certificates (ZIP)
curl -o certificates.zip http://127.0.0.1:8000/jobs/<job_id>/download
```

Downloading a certificate that is failed/pending returns `409`; unknown ids return `404`.

---

## 5. Design decisions

### Processing model: asynchronous background processing
`POST /jobs` validates, stores the job and **returns 202 immediately**; a FastAPI background task then
generates the certificates, and the client polls `GET /jobs/{id}`.

*Why not synchronous?* With thousands of recipients a synchronous request would run for a long time, hit
proxy/client timeouts, and give no progress feedback. (Measured locally: 1,000 certificates ≈ 3 s, so 50,000
would be ≈ 2.5 min – far too long for one HTTP request.)

*Why not Celery/RQ?* It's the right choice for production, but needs a broker and a separate worker process –
extra moving parts for this scope. The code is structured so the switch is a one-liner: `process_job(job_id)`
only needs a job id and opens its own DB session, so `background_tasks.add_task(process_job, id)` becomes
`process_job_task.delay(id)`.

**Restart recovery:** background tasks live in the web process, so a restart could strand a job. On startup the app
re-queues every job still `pending`/`processing`. `process_job` only touches `pending` certificates, so nothing is
generated twice. (Limitation: with several server workers, each would try to resume - a real queue such as Celery
with task locking is the production answer.)

### Validation: two levels
1. **Request-level** (missing `event_name`, empty list, too many recipients, bad types) → whole request rejected with `422`.
2. **Row-level** (bad email, empty/too-long name, duplicate email, non-object row) → *only that row* is stored as
   `failed` with a human-readable error; the valid rows still get generated. This is why `recipients` is parsed
   as raw items in `JobCreate` and validated one by one in `services.validate_recipients`.

Every input row becomes a `certificates` row, so `total` always equals the number of rows submitted and the
client can see exactly what happened to each one (`row_index` maps back to their list).

### Failure isolation
Each certificate is generated inside its own `try/except` and **committed individually**. A failure marks only
that certificate `failed` (with the error message) and processing continues. Committing per certificate also
makes progress visible while the job runs. Job status is derived at the end:
all ok → `completed`, none ok → `failed`, otherwise `completed_with_errors`.

### Data model
* `jobs` – event name, issuer, date, status, timestamps.
* `certificates` – one row per input row: recipient data, `status`, `error`, `file_path`, `row_index`
  (unique per job).

Counts (`succeeded/failed/pending`) are computed with a `GROUP BY` instead of being stored, so they can never
drift out of sync with the real rows.

### Certificate generation
`app/pdf.py` is a pure function (`CertificateData` → PDF bytes) using ReportLab: one fixed landscape-A4
template with a border, recipient name (auto-shrinks for long names), event, date and issuer. No DB or disk
access inside it, so it's trivial to test and replace. PDFs are stored on disk (`storage/<job_id>/<certificate_id>.pdf`,
written atomically); the DB stores only the relative path. File names are server-generated UUIDs, so user input
never reaches the file system path.

### Other choices
* **Pagination + filtering** on the certificate list, since a job may have thousands of rows.
* **ZIP streaming** of successful certificates only, with sortable file names (`0000_Asha_Rao.pdf`).
* A certificate is only retrievable through its own job id (prevents cross-job access by guessing ids).

### Known limitations / what I'd do next
* Built-in PDF fonts only support Western (cp1252) characters. Names in other scripts (e.g. Devanagari) fail
  *that certificate* with a clear error rather than printing garbage. Fix: register a Unicode TTF font
  (e.g. Noto Sans) in `pdf.py`.
* No authentication/rate limiting, no retention/cleanup of old jobs, SQLite by default (use PostgreSQL for
  real concurrency), schema created via `create_all` rather than Alembic migrations.
* Add a `POST /jobs/{id}/retry` endpoint to re-run failed rows after a fix.
* The ZIP is built in memory – for huge jobs, build it on disk or in the background.

## 6. Project layout

```
app/
  main.py       FastAPI app + startup
  api.py        HTTP endpoints
  services.py   validation, job creation, background generation
  pdf.py        certificate template/rendering
  storage.py    file storage helper
  models.py     SQLAlchemy models (Job, Certificate)
  schemas.py    Pydantic request/response models
  database.py   engine/session
  config.py     environment-based settings
tests/          pytest suite (creation, validation, generation, progress, failure isolation, retrieval)
```
