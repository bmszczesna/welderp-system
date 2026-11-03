"""On-demand alert calculation for active orders."""
from datetime import datetime, timedelta, timezone

from backend import models
from backend.schemas import AlertItem


INACTIVE_STATUSES = {models.OrderStatus.COMPLETED, models.OrderStatus.ARCHIVED}


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def calculate_order_alert(
    order: models.Order,
    reference_datetime: datetime,
) -> list[AlertItem]:
    """Return all currently active alerts for one order."""
    if order.status is None or order.status in INACTIVE_STATUSES:
        return []

    reference = _as_naive_utc(reference_datetime)
    if reference is None:
        raise ValueError("reference_datetime must not be None")

    alerts: list[AlertItem] = []
    if order.status == models.OrderStatus.IN_QUEUE:
        queued_at = _as_naive_utc(order.status_changed_at)
        if queued_at is None:
            queued_at = _as_naive_utc(getattr(order, "created_at", None))
        if queued_at is None:
            queued_at = _as_naive_utc(order.date_received)
        if queued_at is not None:
            queued_for = reference - queued_at
            if queued_for > timedelta(days=3):
                days_count = queued_for.days
                alerts.append(AlertItem(
                    order_id=order.id,
                    order_number=order.order_number,
                    client_name=order.client_name,
                    status=order.status,
                    alert_type="queue_delay",
                    message=f"Zlecenie w kolejce od {days_count} dni",
                    days_count=days_count,
                ))

    deadline = _as_naive_utc(order.date_deadline)
    if deadline is not None and deadline < reference:
        days_count = max(0, (reference.date() - deadline.date()).days)
        alerts.append(AlertItem(
            order_id=order.id,
            order_number=order.order_number,
            client_name=order.client_name,
            status=order.status,
            alert_type="deadline_overdue",
            message=f"Przekroczony termin o {days_count} dni",
            days_count=days_count,
        ))

    return alerts
