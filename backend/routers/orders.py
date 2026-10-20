from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend import database, file_storage, models, order_service as service, settings
from backend.models import OrderStatus
from backend.schemas import MATERIAL_UNITS, OrderCreate, OrderResponse, OrderUpdate, StatusEventResponse

router = APIRouter()


@router.get("/metadata/material-units", tags=["Dane produkcyjne"])
def material_units():
    return {"units": MATERIAL_UNITS, "max_pdf_bytes": settings.MAX_PDF_BYTES}


@router.post("/orders/", status_code=201, tags=["Zlecenia"])
def create_order(payload: OrderCreate, db: Session = Depends(database.get_db)):
    order = service.create_order(db, payload)
    return {"message": "Zlecenie utworzone", "order_id": order.id,
            "order_number": order.order_number, "parts": service.created_parts(order, payload)}


@router.get("/orders/", response_model=list[OrderResponse], tags=["Zlecenia"])
def get_orders(db: Session = Depends(database.get_db)):
    return service.order_query(db).order_by(models.Order.id).all()


@router.get("/orders/{order_id}", response_model=OrderResponse, tags=["Zlecenia"])
def get_order(order_id: int, db: Session = Depends(database.get_db)):
    return service.get_order(db, order_id)


@router.get("/orders/{order_id}/history", response_model=list[StatusEventResponse], tags=["Zlecenia"])
def order_history(order_id: int, db: Session = Depends(database.get_db)):
    return service.get_order(db, order_id).status_events


@router.put("/orders/{order_id}", tags=["Zlecenia"])
def update_order(order_id: int, payload: OrderUpdate, db: Session = Depends(database.get_db)):
    order = service.get_order(db, order_id)
    paths = service.update_order(db, order, payload)
    file_storage.remove_files(paths)
    return {"message": "Zlecenie zaktualizowane", "order_id": order.id,
            "parts": service.created_parts(order, payload)}


@router.put("/orders/{order_id}/status", tags=["Zlecenia"])
def update_status(order_id: int, status: str, db: Session = Depends(database.get_db)):
    order = service.get_order(db, order_id)
    try:
        target = OrderStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Nieprawidłowy status") from exc
    if target != order.status:
        if target == OrderStatus.ARCHIVED:
            if order.status != OrderStatus.COMPLETED:
                raise HTTPException(status_code=400, detail="Można archiwizować tylko zakończone zlecenia")
        else:
            service.editable(order)
        if target != OrderStatus.IN_QUEUE:
            service.ensure_material_available(order)
        if target == OrderStatus.COMPLETED and not order.is_approved:
            raise HTTPException(status_code=400, detail="Wymagane jest zatwierdzenie jakości przed zakończeniem")
        service.change_status(db, order, target, "Zmiana statusu na tablicy Kanban")
        if target == OrderStatus.IN_PROGRESS:
            order.needs_correction = False
        service.commit(db)
    return {"message": "Status zaktualizowany", "status": order.status.value}


@router.delete("/orders/{order_id}", tags=["Zlecenia"])
def delete_order(order_id: int, db: Session = Depends(database.get_db)):
    order = service.get_order(db, order_id)
    if order.status != OrderStatus.IN_QUEUE:
        raise HTTPException(status_code=400, detail="Można usunąć tylko zlecenie oczekujące")
    paths = [pdf.file_path for part in order.parts for pdf in part.pdfs]
    db.delete(order)
    service.commit(db)
    file_storage.remove_files(paths)
    return {"message": "Zlecenie usunięte"}


@router.put("/orders/{order_id}/operator", tags=["Zlecenia"])
def assign_operator(order_id: int, operator_name: str, db: Session = Depends(database.get_db)):
    order = service.get_order(db, order_id)
    service.editable(order)
    order.operator = operator_name.strip() or None
    service.commit(db)
    return {"message": "Operator przypisany"}


@router.put("/orders/{order_id}/approve", tags=["Zlecenia"])
def approve_order(order_id: int, db: Session = Depends(database.get_db)):
    order = service.get_order(db, order_id)
    if order.status != OrderStatus.QUALITY_CHECK:
        raise HTTPException(status_code=400, detail="Zlecenie musi być w kontroli jakości")
    service.ensure_material_available(order)
    order.is_approved, order.needs_correction = True, False
    service.commit(db)
    return {"message": "Zlecenie zatwierdzone"}


@router.put("/orders/{order_id}/needs-correction", tags=["Zlecenia"])
def needs_correction(order_id: int, db: Session = Depends(database.get_db)):
    order = service.get_order(db, order_id)
    if order.status != OrderStatus.QUALITY_CHECK:
        raise HTTPException(status_code=400, detail="Zlecenie musi być w kontroli jakości")
    order.is_approved, order.needs_correction = False, True
    service.change_status(db, order, OrderStatus.IN_QUEUE, "Wynik kontroli jakości: do poprawy")
    service.commit(db)
    return {"message": "Zlecenie przeniesione do oczekujących", "status": order.status.value}


@router.put("/parts/{part_id}/material-missing", tags=["Dane produkcyjne"])
def material_missing(part_id: int, missing: bool = True, db: Session = Depends(database.get_db)):
    part = db.get(models.OrderPart, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono części")
    service.editable(part.owner)
    part.material_missing = missing
    if missing:
        part.owner.is_approved = False
        service.change_status(db, part.owner, OrderStatus.IN_QUEUE, f"Brak materiału przy części {part.id}")
    service.commit(db)
    return {"message": "Stan materiału zapisany", "part_id": part.id,
            "material_missing": part.material_missing, "order_status": part.owner.status.value}


@router.post("/parts/{part_id}/upload-pdf", status_code=201, tags=["Dokumentacja PDF"])
async def upload_pdf(part_id: int, file: UploadFile = File(...), db: Session = Depends(database.get_db)):
    part = db.get(models.OrderPart, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono części")
    service.editable(part.owner)
    name, path = await file_storage.save_pdf(file)
    pdf = models.PartPDF(part=part, file_name=name, file_path=path)
    db.add(pdf)
    try:
        service.commit(db)
    except BaseException:
        file_storage.remove_files([path])
        raise
    return {"message": "Plik dodany", "id": pdf.id, "file_name": pdf.file_name, "url": pdf.url}


@router.delete("/parts/pdf/{pdf_id}", tags=["Dokumentacja PDF"])
def delete_pdf(pdf_id: int, db: Session = Depends(database.get_db)):
    pdf = db.get(models.PartPDF, pdf_id)
    if pdf is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono pliku")
    service.editable(pdf.part.owner)
    path = pdf.file_path
    db.delete(pdf)
    service.commit(db)
    file_storage.remove_files([path])
    return {"message": "Plik usunięty"}
