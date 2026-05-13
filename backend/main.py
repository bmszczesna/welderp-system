from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Response
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

genai.configure(api_key="AIzaSyCK2JK6FfJ-9GZZ0vL196k-spy2g2IXRFY")
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

class PartCreate(BaseModel):
    part_name: str
    steps: List[StepCreate] = []


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
    steps: List[StepResponse] = []
    class Config:
        from_attributes = True



class OrderResponse(BaseModel):
    id: int
    order_number: str
    client_name: str
    status: OrderStatus
    date_deadline: Optional[datetime] = None
    operator: Optional[str] = None
    parts: List[PartResponse]

    class Config:
        from_attributes = True

# --- API Endpoints ---

@app.get("/index.html")
def serve_index_html():
    return FileResponse("frontend/index.html")


@app.get("/")
def serve_kanban():
    return FileResponse("frontend/kanban.html")


@app.get("/kanban.html")
def serve_kanban_html():
    return FileResponse("frontend/kanban.html")


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
        new_part = models.OrderPart(part_name=part.part_name, order_id=new_order.id)
        db.add(new_part)
        db.flush()

        for step in part.steps:
            new_step = models.ProcessStep(step_name=step.step_name, part_id=new_part.id)
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
            "operator": o.operator,
            "parts": [
                {
                    "id": p.id,
                    "part_name": p.part_name,
                    "steps": [ { "id": s.id, "step_name": s.step_name, "pdf_path": s.pdf_path } for s in p.steps]

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


@app.post("/steps/{step_id}/upload-pdf")
async def upload_step_pdf(step_id: int, file: UploadFile = File(...), db: Session = Depends(database.get_db)):
    step = db.query(models.ProcessStep).filter(models.ProcessStep.id == step_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Krok nie istnieje")

    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Tylko pliki PDF są dozwolone")

    file_path = os.path.join(UPLOAD_DIR, f"step_{step_id}_{file.filename}")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    step.pdf_path = file_path
    db.commit()
    return {"pdf_path": file_path}


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