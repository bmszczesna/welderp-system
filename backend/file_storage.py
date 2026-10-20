"""Bounded PDF storage with collision-free paths and cleanup on failed writes."""
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from backend import settings

logger = logging.getLogger(__name__)


def resolve_path(stored_path):
    path = Path(stored_path.replace("\\", "/"))
    return path.resolve() if path.is_absolute() else (settings.ROOT_DIR / path).resolve()


def remove_files(paths):
    root = settings.UPLOAD_DIR.resolve()
    for stored_path in paths:
        path = resolve_path(stored_path)
        if not path.is_relative_to(root):
            logger.warning("Skipped cleanup outside the uploads directory")
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove an unused PDF file", exc_info=True)


async def save_pdf(file):
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name.casefold().endswith(".pdf") or not name[:-4].strip() or len(name) > 255:
        raise HTTPException(status_code=400, detail="Wybierz plik z rozszerzeniem .pdf")
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Dozwolone są wyłącznie pliki PDF")

    path = settings.UPLOAD_DIR / f"{uuid4().hex}.pdf"
    total = 0
    try:
        with path.open("xb") as output:
            first = True
            while chunk := await file.read(64 * 1024):
                total += len(chunk)
                if total > settings.MAX_PDF_BYTES:
                    raise HTTPException(status_code=413, detail="Plik PDF przekracza dopuszczalny rozmiar")
                if first and not chunk.startswith(b"%PDF-"):
                    raise HTTPException(status_code=400, detail="Zawartość pliku nie ma nagłówka PDF")
                first = False
                output.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="Plik PDF jest pusty")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    try:
        stored_path = path.relative_to(settings.ROOT_DIR).as_posix()
    except ValueError:
        stored_path = str(path)
    return name, stored_path
