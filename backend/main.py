"""WeldERP API entry point and existing PDF reports.

Order/production validation and transactional writes live in dedicated modules.
Database upgrades run at application startup, not during module import.
"""
from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend import database, models, settings
from backend.integrations import HTML, get_ai_model
from backend.migrations import initialize_database
from backend.models import OrderStatus
from backend.routers import orders, production, users

settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app):
    initialize_database(database.engine)
    yield


app = FastAPI(title="WeldERP - Order Management API", version="0.3.0", lifespan=lifespan)
app.include_router(orders.router)
app.include_router(production.router)
app.include_router(users.router)
app.mount("/frontend", StaticFiles(directory=settings.ROOT_DIR / "frontend"), name="frontend")
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Error contexts may contain exceptions/NaN; do not serialize raw inputs.
    errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
              for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/health", tags=["Uruchomienie"])
def health(db: Session = Depends(database.get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.get("/")
def serve_login():
    return FileResponse(settings.ROOT_DIR / "frontend/login.html")


@app.get("/index.html")
def serve_order_form():
    return FileResponse(settings.ROOT_DIR / "frontend/index.html")


@app.get("/kanban.html")
def serve_kanban(request: Request):
    if request.cookies.get("logged_in") != "true":
        return RedirectResponse(url="/frontend/login.html")
    return FileResponse(settings.ROOT_DIR / "frontend/kanban.html")


@app.get("/archive.html")
def serve_archive():
    return FileResponse(settings.ROOT_DIR / "frontend/archive.html")


@app.get("/orders/{order_id}/pdf")
def generate_order_pdf(order_id: int, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    status_labels = {
        "IN_QUEUE": "Oczekujące",
        "IN_PROGRESS": "W realizacji",
        "QUALITY_CHECK": "Kontrola jakości",
        "COMPLETED": "Zakończone",
        "ARCHIVED": "Archiwum",
    }

    def status_text(status):
        if not status:
            return "-"
        value = status.value if hasattr(status, "value") else str(status)
        return status_labels.get(value, value)

    def format_date(value):
        return value.strftime("%Y-%m-%d") if value else "-"

    def format_datetime(value):
        return value.strftime("%Y-%m-%d %H:%M") if value else "-"

    def yes_no(value):
        return "Tak" if value else "Nie"

    missing_parts = [part for part in order.parts if part.material_missing]
    missing_parts_html = (
        "".join(f"<li>{escape(part.part_name)}</li>" for part in missing_parts)
        if missing_parts
        else "<li>Brak zgłoszonych braków materiałowych</li>"
    )

    correction_html = (
        "<span class='badge badge-danger'>Do poprawy</span>"
        if order.needs_correction
        else "<span class='badge badge-ok'>Brak aktywnej poprawki</span>"
    )

    parts_html = ""
    for part_index, part in enumerate(order.parts, start=1):
        attachment_html = (
            "".join(
                f"""
                <li>
                    <strong>{escape(pdf.file_name)}</strong>
                    <span class="muted">{escape(pdf.url)}</span>
                </li>
                """
                for pdf in part.pdfs
            )
            if part.pdfs
            else "<li>Brak załączników PDF dla tej części</li>"
        )

        steps_rows = ""
        for step in part.steps:
            step_status = "Wykonany" if step.is_completed else "Niewykonany"
            quantity = (
                f"{step.material_quantity:g} {escape(step.material_unit or '')}".strip()
                if step.material_quantity is not None
                else "-"
            )
            material = escape(step.material_used or part.material or "-")
            notes = escape(step.production_notes or "-")

            steps_rows += f"""
                <tr>
                    <td>{escape(step.step_name)}</td>
                    <td>{escape(step.operator or "-")}</td>
                    <td>
                        <span class="badge {'badge-ok' if step.is_completed else 'badge-muted'}">
                            {step_status}
                        </span>
                    </td>
                    <td>{escape(step.completed_by or "-")}</td>
                    <td>{format_datetime(step.completed_at)}</td>
                    <td>{material}</td>
                    <td>{quantity}</td>
                    <td>{notes}</td>
                </tr>
            """

        if not steps_rows:
            steps_rows = """
                <tr>
                    <td colspan="8" class="empty-cell">Brak kroków technologicznych dla tej części</td>
                </tr>
            """

        material_status = (
            "<span class='badge badge-danger'>Brak materiału</span>"
            if part.material_missing
            else "<span class='badge badge-ok'>Materiał dostępny</span>"
        )

        parts_html += f"""
            <section class="part-section">
                <h3>Część {part_index}: {escape(part.part_name)}</h3>
                <table class="summary-table">
                    <tr>
                        <th>Materiał</th>
                        <td>{escape(part.material or "-")}</td>
                        <th>Status materiału</th>
                        <td>{material_status}</td>
                    </tr>
                    <tr>
                        <th>Operator części</th>
                        <td>{escape(part.operator or "-")}</td>
                        <th>Liczba kroków</th>
                        <td>{len(part.steps)}</td>
                    </tr>
                </table>

                <h4>Kroki procesu</h4>
                <table class="steps-table">
                    <thead>
                        <tr>
                            <th>Krok</th>
                            <th>Operator</th>
                            <th>Status</th>
                            <th>Potwierdził</th>
                            <th>Data wykonania</th>
                            <th>Materiał użyty</th>
                            <th>Ilość</th>
                            <th>Uwagi</th>
                        </tr>
                    </thead>
                    <tbody>
                        {steps_rows}
                    </tbody>
                </table>

                <h4>Załączniki PDF</h4>
                <ul class="attachments-list">
                    {attachment_html}
                </ul>
            </section>
        """

    full_html = f"""
    <!DOCTYPE html>
    <html lang="pl">
        <head>
            <meta charset="UTF-8">
            <style>
                @page {{ size: A4; margin: 15mm; }}
                body {{
                    font-family: DejaVu Sans, sans-serif;
                    color: #111827;
                    font-size: 12px;
                    line-height: 1.45;
                }}
                h1 {{
                    color: #991b1b;
                    border-bottom: 2px solid #ef4444;
                    padding-bottom: 8px;
                    margin: 0 0 14px;
                }}
                h2 {{
                    margin: 18px 0 8px;
                    color: #111827;
                    font-size: 16px;
                }}
                h3 {{
                    background: #f3f4f6;
                    border-left: 4px solid #ef4444;
                    padding: 8px 10px;
                    margin: 18px 0 10px;
                    font-size: 14px;
                }}
                h4 {{
                    margin: 12px 0 6px;
                    color: #374151;
                    font-size: 12px;
                    text-transform: uppercase;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 6px 0 12px;
                    page-break-inside: avoid;
                }}
                th, td {{
                    border: 1px solid #e5e7eb;
                    padding: 6px 7px;
                    vertical-align: top;
                }}
                th {{
                    background: #f9fafb;
                    color: #374151;
                    font-weight: 700;
                    text-align: left;
                }}
                .summary-table th {{
                    width: 22%;
                }}
                .steps-table {{
                    font-size: 10px;
                }}
                .steps-table th {{
                    background: #fee2e2;
                }}
                .header-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 10px;
                    margin-bottom: 12px;
                }}
                .info-box {{
                    border: 1px solid #e5e7eb;
                    background: #f9fafb;
                    padding: 10px;
                    border-radius: 6px;
                }}
                .info-box span {{
                    display: block;
                    color: #6b7280;
                    font-size: 10px;
                    text-transform: uppercase;
                    font-weight: 700;
                    margin-bottom: 3px;
                }}
                .info-box strong {{
                    font-size: 13px;
                }}
                .badge {{
                    display: inline-block;
                    padding: 2px 7px;
                    border-radius: 999px;
                    font-size: 10px;
                    font-weight: 700;
                    white-space: nowrap;
                }}
                .badge-ok {{
                    color: #047857;
                    background: #d1fae5;
                    border: 1px solid #86efac;
                }}
                .badge-danger {{
                    color: #991b1b;
                    background: #fee2e2;
                    border: 1px solid #fecaca;
                }}
                .badge-muted {{
                    color: #4b5563;
                    background: #f3f4f6;
                    border: 1px solid #d1d5db;
                }}
                .alerts {{
                    border: 1px solid #fecaca;
                    background: #fff7ed;
                    padding: 10px;
                    border-radius: 6px;
                    margin: 12px 0;
                }}
                .alerts ul {{
                    margin: 6px 0 0 18px;
                    padding: 0;
                }}
                .part-section {{
                    page-break-inside: avoid;
                    margin-top: 14px;
                }}
                .attachments-list {{
                    margin: 0 0 10px 18px;
                    padding: 0;
                }}
                .attachments-list li {{
                    margin-bottom: 4px;
                }}
                .muted {{
                    color: #6b7280;
                    font-size: 10px;
                    margin-left: 6px;
                }}
                .empty-cell {{
                    color: #6b7280;
                    font-style: italic;
                    text-align: center;
                }}
                .footer {{
                    margin-top: 20px;
                    padding-top: 8px;
                    border-top: 1px solid #e5e7eb;
                    color: #6b7280;
                    font-size: 10px;
                    text-align: center;
                }}
            </style>
        </head>
        <body>
            <h1>Karta zlecenia: {escape(order.order_number)}</h1>

            <div class="header-grid">
                <div class="info-box">
                    <span>Klient</span>
                    <strong>{escape(order.client_name or "-")}</strong>
                </div>
                <div class="info-box">
                    <span>Status zlecenia</span>
                    <strong>{escape(status_text(order.status))}</strong>
                </div>
                <div class="info-box">
                    <span>Operator główny</span>
                    <strong>{escape(order.operator or "-")}</strong>
                </div>
                <div class="info-box">
                    <span>Kontrola jakości</span>
                    <strong>{yes_no(order.is_approved)}</strong>
                </div>
                <div class="info-box">
                    <span>Data przyjęcia</span>
                    <strong>{format_date(order.date_received)}</strong>
                </div>
                <div class="info-box">
                    <span>Termin realizacji</span>
                    <strong>{format_date(order.date_deadline)}</strong>
                </div>
            </div>

            <div class="alerts">
                <strong>Braki i poprawki:</strong>
                <p>Poprawka jakościowa: {correction_html}</p>
                <p>Braki materiałowe:</p>
                <ul>{missing_parts_html}</ul>
            </div>

            {parts_html}

            <div class="footer">
                Wygenerowano automatycznie w WeldERP: {datetime.now().strftime('%Y-%m-%d %H:%M')}
            </div>
        </body>
    </html>
    """

    pdf_bytes = HTML(string=full_html).write_pdf()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=zlecenie_{order.order_number}.pdf"
        }
    )



@app.get("/reports/ai-pdf")
def get_ai_report_pdf(db: Session = Depends(database.get_db)):
    # 1. Pobieranie danych z bazy
    ai_model = get_ai_model()
    orders = db.query(models.Order).all()

    production_data = []
    for o in orders:
        production_data.append({
            "nr": o.order_number,
            "klient": o.client_name,
            "status": o.status.value if o.status else "Brak",
            "termin": str(o.date_deadline) if o.date_deadline else "Brak",
            "czesci": [p.part_name for p in o.parts]
        })

    # 2. Generowanie analizy przez AI
    prompt = f"""
    Jesteś ekspertem ds. produkcji WeldERP. Przeanalizuj te dane: {production_data}
    Przygotuj raport:
    1. Statystyki statusów.
    2. Zlecenia zagrożone (terminy).
    3. Wąskie gardła.
    4. Wniosek optymalizacyjny.
    Użyj formatowania HTML (p, br, b, ul, li) w odpowiedzi.
    """

    try:
        ai_response = ai_model.generate_content(prompt)
        ai_analysis_html = ai_response.text
    except Exception as e:
        ai_analysis_html = f"<p>Błąd generowania analizy AI: {str(e)}</p>"

    # 3. Tworzenie szablonu HTML dla PDF
    report_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            @page {{ size: A4; margin: 20mm; }}
            body {{ font-family: DejaVu Sans, sans-serif; line-height: 1.6; color: #1f2937; }}
            h1 {{ color: #8b5cf6; border-bottom: 2px solid #8b5cf6; padding-bottom: 10px; }}
            .report-box {{ background: #f9fafb; padding: 20px; border-radius: 8px; border: 1px solid #e5e7eb; }}
            .footer {{ margin-top: 30px; font-size: 10px; text-align: center; color: #9ca3af; }}
        </style>
    </head>
    <body>
        <h1>Inteligentny Raport Produkcyjny WeldERP</h1>
        <div class="report-box">
            {ai_analysis_html}
        </div>
        <div class="footer">
            Wygenerowano automatycznie przez WeldERP AI • {datetime.now().strftime('%Y-%m-%d %H:%M')}
        </div>
    </body>
    </html>
    """

    # 4. Generowanie PDF
    pdf_bytes = HTML(string=report_html).write_pdf()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=raport_ai_{datetime.now().strftime('%d_%m_%Y')}.pdf"
        }
    )


@app.get("/orders/{order_id}/ai-summary")
def generate_order_ai_summary(
        order_id: int,
        db: Session = Depends(database.get_db)
):
    ai_model = get_ai_model()
    order = db.query(models.Order) \
        .filter(models.Order.id == order_id) \
        .first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    order_data = {
        "numer": order.order_number,
        "klient": order.client_name,
        "operator": order.operator,
        "status": order.status.value if order.status else "Brak",
        "data_otrzymania": str(order.date_received),
        "termin": str(order.date_deadline),
        "czesci": []
    }

    for part in order.parts:
        order_data["czesci"].append({

            "nazwa": part.part_name,
            "operator": part.operator,

            "kroki": [
                step.step_name
                for step in part.steps
            ]
        })

    prompt = f"""
    Jesteś ekspertem produkcji WeldERP.

    Przeanalizuj poniższe zlecenie:

    {order_data}

    Przygotuj profesjonalne podsumowanie:

    1. Opis realizacji zlecenia.
    2. Podsumowanie procesu produkcyjnego.
    3. Możliwe ryzyka lub problemy.
    4. Wnioski organizacyjne.
    5. Krótką ocenę przebiegu produkcji.

    Użyj HTML:
    h2, h3, p, ul, li.
    """

    try:

        ai_response = ai_model.generate_content(
            prompt
        )

        ai_html = ai_response.text

    except Exception as e:

        ai_html = f"""
        <p>
            Błąd generowania AI:
            {str(e)}
        </p>
        """

    full_html = f"""
    <html>

        <head>

            <meta charset="UTF-8">

            <style>

                @page {{
                    size: A4;
                    margin: 20mm;
                }}

                body {{
                    font-family: DejaVu Sans, sans-serif;
                    color: #1f2937;
                    line-height: 1.6;
                }}

                h1 {{
                    color: #8b5cf6;
                    border-bottom: 2px solid #8b5cf6;
                    padding-bottom: 10px;
                }}

                .box {{
                    background: #f9fafb;
                    border: 1px solid #e5e7eb;
                    border-radius: 8px;
                    padding: 20px;
                }}

            </style>

        </head>

        <body>

            <h1>
                Podsumowanie AI
            </h1>

            <div class="box">

                <h2>
                    Zlecenie:
                    {order.order_number}
                </h2>

                {ai_html}

            </div>

        </body>

    </html>
    """

    pdf_bytes = HTML(
        string=full_html
    ).write_pdf()

    return Response(

        content=pdf_bytes,

        media_type="application/pdf",

        headers={
            "Content-Disposition":
                f"attachment; filename=ai_summary_{order.order_number}.pdf"
        }
    )

@app.get("/reports/archive-ai-pdf")
def get_archive_ai_report(

        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        db: Session = Depends(database.get_db)
):

    ai_model = get_ai_model()
    query = db.query(models.Order).filter(
        models.Order.status == OrderStatus.ARCHIVED
    )

    # FILTR DAT OD

    if date_from:

        query = query.filter(

            models.Order.date_received >=
            datetime.fromisoformat(date_from)
        )

    # FILTR DAT DO

    if date_to:

        end_date = datetime.fromisoformat(
            date_to
        ).replace(

            hour=23,
            minute=59,
            second=59
        )

        query = query.filter(

            models.Order.date_received <= end_date
        )

    orders = query.all()

    # STATYSTYKI ERP

    total_orders = len(orders)

    operators = {}

    all_parts = []

    completed_on_time = 0

    total_steps = 0

    for o in orders:

        # operatorzy

        if o.operator:

            operators[o.operator] = (

                operators.get(o.operator, 0) + 1
            )

        # terminowość

        if o.date_deadline and o.date_received:

            if o.date_received <= o.date_deadline:

                completed_on_time += 1

        # części i kroki

        for p in o.parts:

            all_parts.append(p.part_name)

            total_steps += len(p.steps)

    # najaktywniejszy operator

    most_common_operator = (

        max(operators, key=operators.get)

        if operators

        else "Brak"
    )

    # procent terminowości

    on_time_percent = 0

    if total_orders > 0:

        on_time_percent = round(

            (completed_on_time / total_orders) * 100,
            2
        )

    # średnia liczba kroków

    avg_steps = 0

    if total_orders > 0:

        avg_steps = round(
            total_steps / total_orders,
            2
        )

    # dane dla AI

    stats = {

        "liczba_zlecen":
            total_orders,

        "operatorzy":
            operators,

        "najaktywniejszy_operator":
            most_common_operator,

        "liczba_czesci":
            len(all_parts),

        "najczestsze_czesci":
            list(set(all_parts)),

        "procent_terminowych":
            on_time_percent,

        "liczba_krokow_produkcyjnych":
            total_steps,

        "srednia_liczba_krokow":
            avg_steps
    }

    # PROMPT AI

    prompt = f"""
    Jesteś analitykiem biznesowym systemu ERP
    dla firmy produkcyjnej.

    Przeanalizuj dane produkcyjne:

    {stats}

    Przygotuj profesjonalny raport zarządczy.

    Raport ma zawierać:

    1. Podsumowanie operacyjne.
    2. Analizę wydajności operatorów.
    3. Analizę terminowości produkcji.
    4. KPI produkcyjne.
    5. Możliwe ryzyka organizacyjne.
    6. Wąskie gardła produkcyjne.
    7. Rekomendacje optymalizacyjne.
    8. Executive summary.

    Wymagania:

    - używaj procentów,
    - używaj danych liczbowych,
    - używaj języka biznesowego ERP,
    - twórz profesjonalne wnioski,
    - NIE wymyślaj danych,
    - analizuj wyłącznie dostarczone statystyki.

    Użyj HTML:
    h1, h2, h3, p, ul, li, table.
    """

    try:

        ai_response = ai_model.generate_content(
            prompt
        )

        ai_html = ai_response.text

    except Exception as e:

        ai_html = f"""
        <p>
            Błąd AI:
            {str(e)}
        </p>
        """

    # HTML PDF

    full_html = f"""
    <html>

        <head>

            <meta charset="UTF-8">

            <style>

                @page {{
                    size: A4;
                    margin: 20mm;
                }}

                body {{
                    font-family: DejaVu Sans, sans-serif;
                    line-height: 1.6;
                    color: #1f2937;
                }}

                h1 {{
                    color: #8b5cf6;
                    border-bottom: 2px solid #8b5cf6;
                    padding-bottom: 10px;
                }}

                h2 {{
                    color: #111827;
                    margin-top: 25px;
                }}

                .box {{
                    background: #f9fafb;
                    border: 1px solid #e5e7eb;
                    border-radius: 10px;
                    padding: 24px;
                }}

                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 15px;
                }}

                th, td {{
                    border: 1px solid #d1d5db;
                    padding: 10px;
                    text-align: left;
                }}

                th {{
                    background: #ede9fe;
                }}

                .footer {{
                    margin-top: 30px;
                    font-size: 10px;
                    color: #9ca3af;
                    text-align: center;
                }}

            </style>

        </head>

        <body>

            <h1>
                Raport Archiwum WeldERP AI
            </h1>

            <div class="box">

                <p>

                    <strong>
                        Zakres raportu:
                    </strong>

                    {date_from or "początek"}
                    -
                    {date_to or "koniec"}

                </p>

                <p>

                    <strong>
                        Liczba analizowanych zleceń:
                    </strong>

                    {total_orders}

                </p>

                {ai_html}

            </div>

            <div class="footer">

                Wygenerowano automatycznie przez
                WeldERP AI •
                {datetime.now().strftime('%Y-%m-%d %H:%M')}

            </div>

        </body>

    </html>
    """

    pdf_bytes = HTML(
        string=full_html
    ).write_pdf()

    return Response(

        content=pdf_bytes,

        media_type="application/pdf",

        headers={

            "Content-Disposition":
                "attachment; filename=archive_ai_report.pdf"
        }
    )
