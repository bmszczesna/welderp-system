"""Configuration shared by the API, database and command-line tools."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(ROOT_DIR / 'test.db').as_posix()}"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or ROOT_DIR / "uploads").resolve()
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(10 * 1024 * 1024)))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-3-flash-preview")
