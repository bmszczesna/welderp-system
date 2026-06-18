from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Float, Text
from sqlalchemy.orm import relationship
from backend.database import Base
import datetime
from sqlalchemy import Enum
import enum
from passlib.context import CryptContext

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

    # Status
    status = Column(Enum(OrderStatus), default=OrderStatus.IN_QUEUE)
    is_approved = Column(Boolean, default=False)
    needs_correction = Column(Boolean, default=False)

    operator = Column(String, nullable=True)

    # Relationship: One order can have multiple parts
    parts = relationship("OrderPart", back_populates="owner", cascade="all, delete-orphan", order_by="OrderPart.id")


class OrderPart(Base):
    __tablename__ = "order_parts"

    id = Column(Integer, primary_key=True, index=True)
    part_name = Column(String, nullable=False)
    material = Column(String, nullable=True)
    material_missing = Column(Boolean, default=False)
    material_quantity = Column(Float, nullable=True)
    material_unit = Column(String, nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    operator = Column(String, nullable=True)

    pdfs = relationship("PartPDF", back_populates="part", cascade="all, delete-orphan")

    owner = relationship("Order", back_populates="parts")
    steps = relationship("ProcessStep", back_populates="owner_part", cascade="all, delete-orphan", order_by="ProcessStep.id")

class ProcessStep(Base):
    __tablename__ = "process_steps"

    id = Column(Integer, primary_key=True, index=True)
    step_name = Column(String, nullable=False)
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

class Operator(Base):
    __tablename__ = "operators"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
