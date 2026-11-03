from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import alerts, database, models
from backend.main import app
from backend.migrations import initialize_database
from backend.routers import alerts as alerts_router
from backend.seed_demo import PREFIX, SCENARIOS, seed_demo
from backend.schemas import AlertsListResponse


REFERENCE_DATE = date(2026, 11, 3)
REFERENCE_DATETIME = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)


@pytest.fixture
def demo_database(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
    )
    initialize_database(engine)
    seed_demo(engine, reference_date=REFERENCE_DATE)
    yield engine
    engine.dispose()


@pytest.fixture
def demo_client(demo_database):
    with TestClient(app) as client:
        yield client


def get_demo_order(db, number):
    return db.scalar(
        select(models.Order).where(models.Order.order_number == f"{PREFIX}{number:03d}")
    )


def test_all_seeded_orders_match_their_scenarios(demo_database):
    with Session(demo_database) as db:
        for index, scenario in enumerate(SCENARIOS, start=1):
            order = get_demo_order(db, index)
            assert order is not None
            assert order.status == scenario.status
            assert (order.date_received.date() - REFERENCE_DATE).days == scenario.received
            assert (order.date_deadline.date() - REFERENCE_DATE).days == scenario.deadline
            if scenario.completed is None:
                assert order.completed_at is None
            else:
                assert order.completed_at == datetime.combine(
                    REFERENCE_DATE + timedelta(days=scenario.completed), time(14)
                )
            if scenario.no_status_date:
                assert order.status_changed_at is None
                assert order.status_events == []
            else:
                assert order.status_changed_at is not None
                assert order.status_events


def test_demo_deadline_threshold_scenarios(demo_database):
    with Session(demo_database) as db:
        expected_deltas = {21: 9, 22: -10, 25: 0}
        for number, expected_days in expected_deltas.items():
            order = get_demo_order(db, number)
            assert order is not None
            assert order.status == models.OrderStatus.IN_PROGRESS
            assert (order.date_deadline.date() - REFERENCE_DATE).days == expected_days


def test_demo_completed_orders_have_completion_timestamps(demo_database):
    with Session(demo_database) as db:
        completed_orders = [
            get_demo_order(db, number) for number in (13, 14, 23)
        ]
        assert all(order is not None for order in completed_orders)
        assert all(order.status == models.OrderStatus.COMPLETED for order in completed_orders)
        assert all(order.completed_at is not None for order in completed_orders)


def test_legacy_order_without_status_date_has_no_history_and_is_alert_safe(demo_database):
    with Session(demo_database) as db:
        order = get_demo_order(db, len(SCENARIOS))
        assert order is not None
        assert order.status_changed_at is None
        assert order.status_events == []

        calculated = alerts.calculate_order_alert(order, REFERENCE_DATETIME)
        queue_alert = next(item for item in calculated if item.alert_type == "queue_delay")
        assert queue_alert.days_count == 8
        assert AlertsListResponse(total_alerts=len(calculated), items=calculated)


def test_queue_alert_threshold_and_missing_dates_are_safe():
    order = models.Order(
        id=1,
        order_number="QUEUE/TEST",
        client_name="Test",
        status=models.OrderStatus.IN_QUEUE,
        status_changed_at=REFERENCE_DATETIME.replace(tzinfo=None) - timedelta(days=3),
        date_received=None,
    )
    assert alerts.calculate_order_alert(order, REFERENCE_DATETIME) == []

    order.status_changed_at -= timedelta(minutes=1)
    queue_alert = alerts.calculate_order_alert(order, REFERENCE_DATETIME)[0]
    assert queue_alert.alert_type == "queue_delay"
    assert queue_alert.days_count == 3

    order.status_changed_at = None
    assert alerts.calculate_order_alert(order, REFERENCE_DATETIME) == []


def test_alert_api_returns_computed_demo_alerts(demo_client, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return REFERENCE_DATETIME.replace(tzinfo=None)
            return REFERENCE_DATETIME.astimezone(tz)

    monkeypatch.setattr(alerts_router, "datetime", FrozenDateTime)
    response = demo_client.get("/api/alerts")

    assert response.status_code == 200
    result = AlertsListResponse.model_validate(response.json())
    assert result.total_alerts == 7
    assert result.total_alerts == len(result.items)

    by_order_and_type = {(item.order_number, item.alert_type): item for item in result.items}
    assert ("DEMO/004", "queue_delay") in by_order_and_type
    assert ("DEMO/004", "deadline_overdue") in by_order_and_type
    assert ("DEMO/022", "deadline_overdue") in by_order_and_type
    assert ("DEMO/025", "deadline_overdue") in by_order_and_type
    assert ("DEMO/026", "queue_delay") in by_order_and_type
    assert all(
        item.status not in (models.OrderStatus.COMPLETED, models.OrderStatus.ARCHIVED)
        for item in result.items
    )
