"""Load optional reporting integrations only when a report is requested."""
import os

from fastapi import HTTPException

from backend.settings import GEMINI_MODEL


def get_ai_model():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Raporty AI wymagają ustawienia GEMINI_API_KEY w .env")
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Brak biblioteki obsługującej raporty AI") from exc
    genai.configure(api_key=key)
    return genai.GenerativeModel(GEMINI_MODEL)


def HTML(*args, **kwargs):
    try:
        from weasyprint import HTML as WeasyHTML
    except (ImportError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Brak zależności generatora PDF. Uruchom aplikację przez Docker Compose.") from exc
    return WeasyHTML(*args, **kwargs)
