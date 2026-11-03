from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import alerts as alert_service, database, models
from backend.schemas import AlertsListResponse

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])


@router.get("", response_model=AlertsListResponse)
def list_alerts(db: Session = Depends(database.get_db)):
    active_statuses = (
        models.OrderStatus.COMPLETED,
        models.OrderStatus.ARCHIVED,
    )
    orders = db.scalars(
        select(models.Order)
        .where(models.Order.status.notin_(active_statuses))
        .order_by(models.Order.id)
    )
    reference_datetime = datetime.now(timezone.utc)
    items = [
        alert
        for order in orders
        for alert in alert_service.calculate_order_alert(order, reference_datetime)
    ]
    return AlertsListResponse(total_alerts=len(items), items=items)
