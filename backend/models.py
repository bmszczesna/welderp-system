from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Float, Text
from sqlalchemy.orm import relationship
from backend.database import Base
import datetime
from sqlalchemy import Enum
import enum
from passlib.context import CryptContext
from pathlib import Path
from urllib.parse import quote

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)

class OrderStatus(enum.Enum):
    IN_QUEUE = "IN_QUEUE"
    IN_PROGRESS = "IN_PROGRESS"
    QUALITY_CHECK = "QUALITY_CHECK"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    # Unique order number
    order_number = Column(String, unique=True, index=True, nullable=False)
    client_name = Column(String)

    # Tracking dates
    date_received = Column(DateTime, default=datetime.datetime.utcnow)
    date_deadline = Column(DateTime)
    # Legacy rows remain NULL: their previous status-change time is unknown.
    status_changed_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Status
    status = Column(Enum(OrderStatus), default=OrderStatus.IN_QUEUE)
    is_approved = Column(Boolean, default=False)
    needs_correction = Column(Boolean, default=False)

    operator = Column(String, nullable=True)

    # Relationship: One order can have multiple parts
    parts = relationship("OrderPart", back_populates="owner", cascade="all, delete-orphan", order_by="(OrderPart.position, OrderPart.id)")
    status_events = relationship("OrderStatusEvent", back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusEvent.id")


class OrderPart(Base):
    __tablename__ = "order_parts"

    id = Column(Integer, primary_key=True, index=True)
    part_name = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    material = Column(String, nullable=True)
    material_missing = Column(Boolean, default=False)
    order_id = Column(Integer, ForeignKey("orders.id"))
    operator = Column(String, nullable=True)

    pdfs = relationship("PartPDF", back_populates="part", cascade="all, delete-orphan")

    owner = relationship("Order", back_populates="parts")
    steps = relationship("ProcessStep", back_populates="owner_part", cascade="all, delete-orphan", order_by="(ProcessStep.position, ProcessStep.id)")

class ProcessStep(Base):
    __tablename__ = "process_steps"

    id = Column(Integer, primary_key=True, index=True)
    step_name = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    status = Column(Enum(OrderStatus), default=OrderStatus.IN_QUEUE)
    operator = Column(String, nullable=True)

    part_id = Column(Integer, ForeignKey("order_parts.id"))
    owner_part = relationship("OrderPart", back_populates="steps")
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String, nullable=True)
    production_notes = Column(Text, nullable=True)
    material_used = Column(String, nullable=True)
    material_quantity = Column(Float, nullable=True)
    material_unit = Column(String, nullable=True)


class PartPDF(Base):
    __tablename__ = "part_pdfs"
    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    part_id = Column(Integer, ForeignKey("order_parts.id"))
    part = relationship("OrderPart", back_populates="pdfs")

    @property
    def url(self):
        return "/uploads/" + quote(Path(self.file_path.replace("\\", "/")).name)


class OrderStatusEvent(Base):
    __tablename__ = "order_status_events"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    from_status = Column(Enum(OrderStatus), nullable=True)
    to_status = Column(Enum(OrderStatus), nullable=False)
    occurred_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    reason = Column(String, nullable=False)
    order = relationship("Order", back_populates="status_events")

class Operator(Base):
    __tablename__ = "operators"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
