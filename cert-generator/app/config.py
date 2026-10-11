"""Central configuration, overridable through environment variables."""
import os
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./certificates.db")
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage"))
MAX_RECIPIENTS_PER_JOB = int(os.getenv("MAX_RECIPIENTS_PER_JOB", "5000"))
