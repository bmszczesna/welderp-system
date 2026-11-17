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
git clone --branch main https://github.com/bmszczesna/welderp-system.git
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
z raportów Gemini, ustaw własny `GEMINI_API_KEY` w lokalnym pliku `.env`.
Pliki `.env`, dane logowania i lokalne kopie bazy nie są publikowane.

Przy pierwszym uruchomieniu zarejestruj własne konto przez formularz logowania.
Pierwsze konto w pustej bazie otrzymuje rolę kierownika, kolejne rolę operatora.
Przy aktualizacji starszej bazy kierownikiem zostaje najstarsze istniejące konto;
hasła pozostają bez zmian. Sesja logowania trwa osiem godzin i jest weryfikowana
po stronie serwera. Po aktualizacji zaloguj się ponownie.
Obraz zawiera zależności WeasyPrint/Pango i fonty potrzebne do generowania PDF.

## Pełna wersja demonstracyjna od ekranu logowania

Przy działającym Docker Desktop uruchom z katalogu projektu:

```powershell
py scripts/prepare_preview.py
```

Otwórz http://127.0.0.1:8016/ i zaloguj się:

| Konto | Login | Hasło demonstracyjne |
| --- | --- | --- |
| Kierownik | `kierownik_demo` | `KierownikDemo123!` |
| Operator | `operator_demo` | `OperatorDemo123!` |

Skrypt uruchamia osobny projekt Compose `welderp-magazyn`, dodaje brakujące
przykładowe zlecenia, operatorów, materiały, plany zużycia i dostawę. Nie nadpisuje
istniejących haseł ani stanów magazynowych. Dane pozostają w nazwanym woluminie
`welderp-magazyn_warehouse_preview_data` między uruchomieniami.
Przed uzupełnieniem danych istniejącej bazy powstaje pełna kopia w `backups/`.
Pozostałe projekty Docker pozostają niezależne. Konta demonstracyjne służą do pracy lokalnej.

Zatrzymanie i ponowny start gotowej wersji:

```powershell
docker compose -f docker-compose.preview.yml stop
docker compose -f docker-compose.preview.yml up -d
```

## Magazyn i prognozy

Przycisk **Magazyn** na tablicy Kanban otwiera `/frontend/warehouse.html`.
Konto operatora nie widzi tego przycisku, a API magazynu odrzuca dostęp bez roli kierownika.
Każda pozycja pokazuje ilość na stanie, ilość zarezerwowaną dla zleceń, wolny zapas
i minimum. Rezerwacja powstaje od razu przy zapisaniu zlecenia i nie zmniejsza stanu fizycznego.

1. Dodaj materiał, np. `S235JR`, jednostkę `kg`, stan początkowy `100` i minimum `20`.
2. Dostawę zapisz przyciskiem **Przyjmij**. W karcie części podczas realizacji wpisz łączną zużytą ilość i kliknij **Zapisz zużycie** — magazyn rozliczy się automatycznie.
3. Kierownik nadal może użyć **Wydaj** w magazynie. Wydanie przypisane do części aktualizuje także jej zużycie, więc nie wpisuje się go drugi raz.
4. **Historia** pokazuje przyjęcia, rozchody, zlecenie i stan po każdym ruchu.

Podczas dodawania lub edycji zlecenia wybierz przy każdej części materiał z magazynu
i potrzebną ilość. Nie podaje się osobnej daty rezerwacji. Wybór jest dostępny również
dla operatora tworzącego zlecenie; obsługa stanów magazynowych pozostaje dostępna kierownikowi.

Zlecenie rezerwuje tylko wolny materiał. Przy zbyt małej ilości zapisuje się w oczekujących
z ostrzeżeniem, ile brakuje. Nie można przesunąć go do realizacji ani kolejnych kolumn
do czasu uzupełnienia zapasu. Blokadę sprawdza także serwer. Brak wyboru materiału lub
potrzebnej ilości również blokuje dalszą pracę, w tym przy starszych zleceniach.

Do kontroli jakości można przekazać tylko zlecenie w realizacji, z wykonanymi wszystkimi
operacjami i dodatnim zużyciem zapisanym przy każdej części. Zużycie musi być zgodne
z jednostką materiału i rozliczone w historii magazynu; sam dawny wpis produkcyjny
nie wystarcza. Te same warunki serwer sprawdza ponownie przy zatwierdzeniu jakości
i zakończeniu. Zakończenie wymaga zatwierdzenia w kontroli jakości. Rzeczywiste zużycie
może być mniejsze od planowanego — zakończenie zwalnia wyłącznie niewykorzystaną rezerwację,
bez dodatkowego wydania materiału. Zero służy do wycofania zużycia, nie do zamknięcia produkcji.

Po przyjęciu materiału system automatycznie uzupełnia brakujące rezerwacje według kolejności
utworzenia części. Istniejące rezerwacje są zachowane, więc edycja starszego zlecenia
nie zabiera zapasu innym. Zmniejszenie potrzeb, usunięcie części lub zlecenia oraz zamknięcie
zlecenia zwalniają rezerwację. Wydanie na część jednocześnie zmniejsza stan, jej rezerwację
i pozostałe zapotrzebowanie. Rozchód ogólny może pobrać wyłącznie wolny materiał.

Datę prognozy wybiera się w kalendarzu, od dziś do 365 dni naprzód. Przyciski „Dziś”,
„Za 7 dni”, „Za 14 dni” i „Za 30 dni” pozwalają szybko ustawić datę. Tabela pokazuje
stan na koniec wybranego dnia, pierwszy dzień wyczerpania zapasów do tej daty,
ilość do zamówienia i dzienny bilans w szczegółach. Wynik uwzględnia wszystkie
potrzeby i dostawy od dziś do wybranej daty włącznie, a nie tylko zdarzenia z tego dnia.
Uwzględnia bieżący stan, jeszcze
niewydane potrzebne ilości aktywnych zleceń oraz zaplanowane dostawy. Zużycie jest
prognozowane według terminu zlecenia; rezerwacja nie czeka na tę datę. Zakończone
i archiwalne zlecenia nie tworzą zapotrzebowania. Zaległe potrzeby trafiają na dziś;
spóźnione dostawy są sygnalizowane i nie zwiększają prognozowanego stanu.
Ostrzeżenie wskazuje części bez kompletnego planu, których potrzeb prognoza nie obejmuje.

Ilość do zamówienia uzupełnia najniższy przewidywany stan na koniec dnia do ustawionego
minimum w wybranym okresie. Planowanie nie uwzględnia terminów dostawców ani kolejności
zdarzeń wewnątrz dnia. Jeśli zapas wyczerpie się wcześniej, a późniejsza dostawa go
uzupełni, sugestia nadal uwzględnia wcześniejszy brak. Plan dostawy jest wpisem w systemie;
fizyczny stan zwiększa się
dopiero po **Przyjmij dostawę**. Przyjęcie jest jednokrotne, a otrzymana dostawa przestaje
być liczona jako przyszły przychód. Wysłanie zamówienia do dostawcy odbywa się poza aplikacją.

Rozchód przekraczający wolny stan lub rezerwację wskazanej części jest odrzucany. Ilości przyjmują przecinek lub kropkę
i maksymalnie sześć miejsc po przecinku. Materiał z taką samą nazwą i jednostką może wystąpić
tylko raz; jednostki są normalizowane, ale nie są przeliczane między sobą.

**Zapisz zużycie** rozlicza łączną ilość, a nie kolejną porcję: zapis 2,4 kg odejmuje
2,4 kg ze stanu i rezerwacji. Ponowny zapis 2,4 kg niczego drugi raz nie odejmuje.
Zmiana na 3 kg pobiera kolejne 0,6 kg; zmiana na 2 kg zwraca 1 kg do magazynu i przywraca
potrzebną rezerwację tej części. Wpisanie 0 wycofuje całe zużycie. Historia zachowuje
wydania i zwroty z opisem korekty. Zapis obejmuje stan, rezerwację i zużycie w jednej transakcji.
Ilość ponad plan można zużyć tylko z wolnego zapasu; rezerwacje innych zleceń są chronione.
Jednostka zużycia musi odpowiadać jednostce wybranego materiału. Prognoza liczy pozostałe
potrzeby po uwzględnieniu zarówno wydań, jak i zwrotów.

Stare zapisy produkcyjne nie zmniejszają automatycznie magazynu podczas aktualizacji
aplikacji. Jeśli nie mają powiązanego wydania, karta pokazuje informację i pozwala
je rozliczyć świadomym zapisem podczas realizacji. Istniejące wydania przypisane do części
są uwzględniane, więc ten sam materiał nie zostaje pobrany dwukrotnie.
Zapis zużycia wymaga zalogowania (także operator może go wykonać). Pole `expected_version`
w API chroni formularz przed nadpisaniem nowszego zużycia z drugiego okna.
Ponowienie tego samego zgłoszenia ręcznego przyjęcia lub rozchodu z tym samym `request_id`
nie tworzy drugiego ruchu.
Nowe tabele powstają automatycznie podczas startu aplikacji, z pustymi stanami.

API magazynu: `GET/POST /api/warehouse/materials`,
`POST/GET /api/warehouse/materials/{id}/movements`,
`PATCH /api/warehouse/materials/{id}/minimum`, `GET /api/warehouse/forecast`,
`GET /api/warehouse/demands`, `GET/POST /api/warehouse/deliveries`,
`POST /api/warehouse/deliveries/{id}/receive` i `/cancel`.

Prognoza na konkretny dzień: `GET /api/warehouse/forecast?target_date=YYYY-MM-DD`.
Starszy parametr `days` pozostaje obsługiwany. Widok części pokazuje potrzebną ilość,
pozostałą rezerwację i zużycie odjęte z magazynu. Zamknięte zlecenia
nie pokazują wezwania do uzupełniania powiązania z magazynem.

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

Dodaj `--with-warehouse`, aby dołączyć konta kierownika i operatora, pozycje magazynowe
i plany materiałowe demonstracyjnych zleceń. Ten skrypt korzysta z podstawowego
`docker-compose.yml`; do oddzielnego podglądu na porcie 8016 służy `prepare_preview.py`.

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

Opcja `--with-warehouse` dodaje również demonstracyjny magazyn i plany materiałowe.

Przed pierwszym startem nowej wersji wykonaj kopię dotychczasowej bazy i `uploads`.
Przy starcie aplikacja addytywnie uzupełnia brakujące kolumny i tabele.
