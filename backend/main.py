from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response, Request
from sqlalchemy.orm import Session
import shutil
import os
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from backend import models
from backend import database
from backend.models import OrderStatus
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from weasyprint import HTML
import io
import google.generativeai as genai

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("BŁĄD: Brak klucza API Gemini w zmiennych środowiskowych!")
else:
    genai.configure(api_key=api_key)

ai_model = genai.GenerativeModel('models/gemini-3-flash-preview')
models.Base.metadata.create_all(bind=database.engine)

UPLOAD_DIR = "uploads"

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

app = FastAPI(title="WeldERP - Order Management API")

app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StepCreate(BaseModel):
    step_name: str
    operator: Optional[str] = None

class PartCreate(BaseModel):
    part_name: str
    steps: List[StepCreate] = []
    operator: Optional[str] = None



class OrderCreate(BaseModel):
    order_number: str
    client_name: str
    date_received: Optional[datetime] = None
    date_deadline: Optional[datetime] = None
    operator: Optional[str] = None
    parts: List[PartCreate] = []

class StepResponse(BaseModel):
    id: int
    step_name: str
    pdf_path: Optional[str] = None
    class Config:
        from_attributes = True

class PartResponse(BaseModel):
    id: int
    part_name: str
    operator: Optional[str] = None
    steps: List[StepResponse] = []
    class Config:
        from_attributes = True



class OrderResponse(BaseModel):
    id: int
    order_number: str
    client_name: str
    status: OrderStatus
    is_approved: bool = False

    date_received: Optional[datetime] = None
    date_deadline: Optional[datetime] = None

    operator: Optional[str] = None
    parts: List[PartResponse]

    class Config:
        from_attributes = True



class StepUpdate(BaseModel):
    step_name: str
    operator: Optional[str] = None

class PartUpdate(BaseModel):
    part_name: str
    operator: Optional[str] = None
    steps: List[StepCreate] = []

class OrderUpdate(BaseModel):
    order_number: str
    client_name: str
    date_deadline: Optional[datetime] = None
    operator: Optional[str] = None
    parts: List[PartUpdate] = []

# --- API Endpoints ---

@app.get("/index.html")
def serve_index_html():
    return FileResponse("frontend/index.html")


@app.get("/")
def serve_kanban():
    return FileResponse("frontend/login.html")


@app.get("/kanban.html")
def serve_kanban_html(request: Request):
    if "logged_in" not in request.cookies:
        return RedirectResponse(url="/frontend/login.html")
    return FileResponse("frontend/kanban.html")

@app.get("/archive.html")
def serve_archive_html():
    return FileResponse("frontend/archive.html")


@app.post("/orders/", status_code=201)
def create_order(order: OrderCreate, db: Session = Depends(database.get_db)):
    #
    db_order = db.query(models.Order).filter(models.Order.order_number == order.order_number).first()
    if db_order:
        raise HTTPException(status_code=400, detail="Order number already exists")


    new_order = models.Order(
        order_number=order.order_number,
        client_name=order.client_name,
        date_received=order.date_received,
        date_deadline=order.date_deadline,
        operator=order.operator
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    for part in order.parts:
        new_part = models.OrderPart(part_name=part.part_name, order_id=new_order.id, operator=part.operator)
        db.add(new_part)
        db.flush()

        for step in part.steps:
            new_step = models.ProcessStep(step_name=step.step_name, part_id=new_part.id, operator=step.operator)
            db.add(new_step)

    db.commit()

    return {"message": "Order created successfully", "order_id": new_order.id, "order_number": new_order.order_number}

@app.get("/orders/")
def get_orders(db: Session = Depends(database.get_db)):
    orders = db.query(models.Order).all()

    result = []

    for o in orders:
        result.append({
            "id": o.id,
            "order_number": o.order_number,
            "client_name": o.client_name,
            "date_received": o.date_received,
            "date_deadline": o.date_deadline,
            "status": o.status.value if o.status else None,
            "is_approved": o.is_approved,
            "operator": o.operator,
            "parts": [
                {
                    "id": p.id,
                    "part_name": p.part_name,
                    "operator": p.operator,
                    "steps": [ { "id": s.id, "step_name": s.step_name, "operator": s.operator} for s in p.steps],
                    "pdfs": [{"id": pdf.id, "file_name": pdf.file_name} for pdf in p.pdfs]
                }
                for p in o.parts
            ]
        })

    return result


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return order

@app.put("/orders/{order_id}/status")
def update_status(order_id: int, status: str, db: Session = Depends(database.get_db)):

    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        order.status = OrderStatus(status)
    except:
        raise HTTPException(status_code=400, detail="Invalid status")

    db.commit()
    db.refresh(order)

    return {"message": "Status updated", "status": order.status.value}

@app.delete("/orders/{order_id}")
def delete_order(order_id: int, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    db.delete(order)
    db.commit()

    return {"message": "Order deleted"}

@app.put("/orders/{order_id}/operator")
def assign_operator(order_id: int, operator_name: str, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Nie znaleziono zlecenia")
    
    order.operator = operator_name if operator_name != "Brak" else None
    db.commit()
    return {"message": "Operator przypisany"}

@app.put("/orders/{order_id}/approve")
def approve_order(order_id: int, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Nie znaleziono zlecenia")
    
    order.is_approved = True
    db.commit()
    return {"message": "Zlecenie zatwierdzone"}

@app.put("/orders/{order_id}")
def update_order(order_id: int, order_update: OrderUpdate, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.order_number = order_update.order_number
    order.client_name = order_update.client_name
    order.date_deadline = order_update.date_deadline
    order.operator = order_update.operator

    for part in order.parts:
        db.delete(part)
    db.commit()

    for part in order_update.parts:
        new_part = models.OrderPart(part_name=part.part_name, order_id=order.id, operator=part.operator)
        db.add(new_part)
        db.flush()

        for step in part.steps:
            new_step = models.ProcessStep(step_name=step.step_name, part_id=new_part.id, operator=step.operator)
            db.add(new_step)
    
    db.commit()
    db.refresh(order)
    return {"message": "Order updated successfully"}


@app.post("/parts/{part_id}/upload-pdf")
async def upload_part_pdf(part_id: int, file: UploadFile = File(...), db: Session = Depends(database.get_db)):
    full_filename = f"{part_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, full_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    new_pdf = models.PartPDF(part_id=part_id, file_path=file_path, file_name=full_filename)
    db.add(new_pdf)
    db.commit()
    db.refresh(new_pdf)
    return {"message": "Plik dodany", "id": new_pdf.id}


@app.delete("/parts/pdf/{pdf_id}")
async def delete_part_pdf(pdf_id: int, db: Session = Depends(database.get_db)):
    pdf = db.query(models.PartPDF).filter(models.PartPDF.id == pdf_id).first()
    if not pdf:
        raise HTTPException(status_code=404, detail="Plik nie znaleziony")

    if os.path.exists(pdf.file_path):
        os.remove(pdf.file_path)

    db.delete(pdf)
    db.commit()
    return {"message": "Plik usunięty"}


@app.get("/orders/{order_id}/pdf")
def generate_order_pdf(order_id: int, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    parts_html = ""
    for part in order.parts:
        steps_list = "".join([f"<li>{s.step_name}</li>" for s in part.steps])
        parts_html += f"<h3>Część: {part.part_name}</h3><ul>{steps_list}</ul>"

    full_html = f"""
    <html>
        <head>
            <style>
                @page {{ size: A4; margin: 15mm; }}
                body {{ font-family: sans-serif; }}
                h1 {{ color: #ef4444; border-bottom: 2px solid #ef4444; }}
                h3 {{ background: #f3f4f6; padding: 5px; }}
            </style>
        </head>
        <body>
            <h1>Zlecenie: {order.order_number}</h1>
            <p><strong>Klient:</strong> {order.client_name}</p>
            <p><strong>Termin:</strong> {order.date_deadline.strftime('%Y-%m-%d') if order.date_deadline else '-'}</p>
            <hr>
            {parts_html}
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


@app.put("/steps/{step_id}/toggle")
def toggle_step_status(step_id: int, db: Session = Depends(database.get_db)):
    step = db.query(models.ProcessStep).filter(models.ProcessStep.id == step_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Krok nie istnieje")

    step.is_completed = not step.is_completed
    db.commit()
    return {"is_completed": step.is_completed}

@app.get("/operators/")
def get_operators(db: Session = Depends(database.get_db)):
    return db.query(models.Operator).all()

@app.post("/operators/")
def add_operator(name: str, db: Session = Depends(database.get_db)):
    new_op = models.Operator(name=name)
    db.add(new_op)
    db.commit()
    return new_op

@app.delete("/operators/{op_id}")
def delete_operator(op_id: int, db: Session = Depends(database.get_db)):
    op = db.query(models.Operator).filter(models.Operator.id == op_id).first()
    if not op:
        raise HTTPException(status_code=404, detail="Operator nie istnieje")

    assigned_orders = db.query(models.Order).filter(
        models.Order.operator == op.name,
        models.Order.status.notin_([OrderStatus.COMPLETED, OrderStatus.ARCHIVED])
    ).first()

    if assigned_orders:
        raise HTTPException(
            status_code=400,
            detail=f"Nie można usunąć operatora {op.name}, ponieważ jest przypisany do trwających zleceń."
        )

    db.delete(op)
    db.commit()
    return {"message": "Operator usunięty"}

@app.post("/register/")
def register_user(username: str, password: str, db: Session = Depends(database.get_db)):
    hashed_pw = models.pwd_context.hash(password)
    user = models.User(username=username, hashed_password=hashed_pw)
    db.add(user)
    db.commit()
    return {"message": "Użytkownik zarejestrowany"}


@app.post("/login/")
def login_user(username: str, password: str, response: Response, db: Session = Depends(database.get_db)):
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not models.pwd_context.verify(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Nieprawidłowy login lub hasło")

    response.set_cookie(key="logged_in", value="true", httponly=True)
    return {"message": "Zalogowano pomyślnie"}


@app.post("/logout/")
def logout(response: Response):
    response.delete_cookie("logged_in")
    return {"message": "Wylogowano"}


# Sprawdź, czy masz to w main.py:
@app.post("/register/")
def register_user(username: str, password: str, db: Session = Depends(database.get_db)):
    existing_user = db.query(models.User).filter(models.User.username == username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Użytkownik już istnieje")

    hashed_pw = models.pwd_context.hash(password)
    user = models.User(username=username, hashed_password=hashed_pw)
    db.add(user)
    db.commit()
    return {"message": "Użytkownik zarejestrowany"}