from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import models
import database
from models import OrderStatus

models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="WeldERP - Order Management API")


class PartCreate(BaseModel):
    part_name: str


class OrderCreate(BaseModel):
    order_number: str
    client_name: str
    date_deadline: Optional[datetime] = None
    parts: List[PartCreate] = []

class PartResponse(BaseModel):
    id: int
    part_name: str

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id: int
    order_number: str
    client_name: str
    status: str
    parts: List[PartResponse]

    class Config:
        from_attributes = True

# --- API Endpoints ---
@app.get("/")
def read_root():
    return {"message": "WeldERP API is running. Database is ready for orders."}


@app.post("/orders/", status_code=201)
def create_order(order: OrderCreate, db: Session = Depends(database.get_db)):
    #
    db_order = db.query(models.Order).filter(models.Order.order_number == order.order_number).first()
    if db_order:
        raise HTTPException(status_code=400, detail="Order number already exists")


    new_order = models.Order(
        order_number=order.order_number,
        client_name=order.client_name,
        date_deadline=order.date_deadline
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)

    for part in order.parts:
        new_part = models.OrderPart(part_name=part.part_name, order_id=new_order.id)
        db.add(new_part)

    db.commit()

    return {"message": "Order created successfully", "order_id": new_order.id, "order_number": new_order.order_number}

@app.get("/orders/", response_model=List[OrderResponse])
def get_orders(db: Session = Depends(database.get_db)):
    return db.query(models.Order).all()

@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return order

@app.put("/orders/{order_id}/status")
def update_status(order_id: int, status: OrderStatus, db: Session = Depends(database.get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.status = status
    db.commit()

    return {"message": "Status updated", "status": status}