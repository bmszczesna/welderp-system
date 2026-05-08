from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from backend import models
from backend import database
from backend.models import OrderStatus
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="WeldERP - Order Management API")

app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")

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

class PartResponse(BaseModel):
    id: int
    part_name: str
    operator: Optional[str] = None
    steps: List[StepCreate] = []
    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: int
    order_number: str
    client_name: str
    status: str
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
            "operator": o.operator,
            "parts": [
                {
                    "id": p.id,
                    "part_name": p.part_name,
                    "operator": p.operator,
                    "steps": [ { "id": s.id, "step_name": s.step_name, "operator": s.operator } for s in p.steps]
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