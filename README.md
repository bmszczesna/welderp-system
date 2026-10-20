# WeldERP System

Aplikacja do obsługi zleceń produkcyjnych: części, operacje technologiczne,
operatorzy, materiały, dokumentacja PDF, kontrola jakości i archiwum.
Zużycie materiału zapisuje się dla całej części; wykonanie jest oznaczane
osobno przy każdej operacji.
Backend: FastAPI i SQLAlchemy. Frontend: HTML, CSS i JavaScript.
Baza: PostgreSQL w Dockerze lub SQLite przy uruchomieniu lokalnym.

## Uruchomienie przez Docker Compose

Wymagane są Git oraz działający Docker Desktop z Docker Compose.
Pobierz aktualną gałąź projektu:

```powershell
git clone --branch testy https://github.com/bmszczesna/welderp-system.git
cd welderp-system
```

```powershell
docker compose up --build -d
```

- Aplikacja: http://localhost:8000
- Swagger: http://localhost:8000/docs
- Sprawdzenie bazy: http://localhost:8000/health
- PostgreSQL z hosta: `localhost:5431`; wewnątrz Compose: `db:5432`.

Podstawowa aplikacja działa bez pliku `.env` i bez klucza AI. Aby korzystać
z raportów Gemini, skopiuj `.env.example` do `.env` i ustaw własny
`GEMINI_API_KEY`. `.env.example` zawiera wyłącznie wzór konfiguracji.
Pliki `.env`, dane logowania i lokalne kopie bazy nie są publikowane.

Przy pierwszym uruchomieniu zarejestruj własne konto przez formularz logowania.
Obraz zawiera zależności WeasyPrint/Pango i fonty potrzebne do generowania PDF.

## Dane bazy

`database/welderp_data.sql` zawiera zrzut danych operacyjnych: zlecenia i klientów,
części, operacje, operatorów, historię statusów i rekordy załączników.
Konta użytkowników i hasła są pominięte. Pliki załączników pozostają w lokalnym
katalogu `uploads`; nie są częścią zrzutu SQL.

Importuj zrzut do świeżej bazy po uruchomieniu aplikacji i utworzeniu jej schematu:

```powershell
docker compose cp database/welderp_data.sql db:/tmp/welderp_data.sql
docker compose exec -T db psql -U welderp_user -d welderp_db -v ON_ERROR_STOP=1 --single-transaction -f /tmp/welderp_data.sql
```

Przed importem do bazy zawierającej własne dane wykonaj kopię i uzgodnij
identyfikatory. Zrzut zachowuje ID i stany sekwencji z bazy źródłowej.
Sam zrzut nie tworzy konta do logowania.

Aktualizacja zrzutu z działającego Docker Compose:

```powershell
py scripts/export_database.py
```

Dodanie stałego zestawu demonstracyjnego do działającego Docker Compose
(20 zleceń, 6 klientów, 8 operatorów; bez duplikowania istniejących rekordów):

```powershell
py scripts/load_demo_docker.py
```

Skrypt dodawania danych najpierw zapisuje pełną lokalną kopię PostgreSQL do
ignorowanego katalogu `backups/`. Dane demonstracyjne są oznaczone `DEMO`.

## Praca lokalna i testy

Python 3.11 lub 3.12:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Bez `DATABASE_URL` aplikacja korzysta z lokalnego `test.db` w katalogu projektu.
To odrębna baza od PostgreSQL w Dockerze. Testy mają własną bazę w pamięci
i katalog `.test-artifacts/uploads`; nie korzystają z danych aplikacji.

- `backend/requirements-core.txt`: podstawowa obsługa aplikacji i załączników.
- `backend/requirements-dev.txt`: zależności podstawowe i testowe.
- `backend/requirements.txt`: pełna instalacja z WeasyPrint i Gemini.

Lokalne generowanie PDF wymaga również systemowych bibliotek Pango.
Bez opcjonalnej integracji raportowej funkcja raportu zwraca HTTP 503,
a pozostałe funkcje pozostają dostępne. Test natywnego PDF jest wtedy pomijany.

Lokalne dane demonstracyjne SQLite można dodać poleceniem:

```powershell
.\.venv\Scripts\python.exe -m backend.seed_demo
```

Przed pierwszym startem nowej wersji wykonaj kopię dotychczasowej bazy i `uploads`.
Przy starcie aplikacja addytywnie uzupełnia brakujące kolumny i tabele.
