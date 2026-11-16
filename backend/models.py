from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Boolean, Float, Text, Numeric, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship, column_property
from sqlalchemy import select, func
from backend.database import Base
import datetime
from sqlalchemy import Enum
import enum
from passlib.context import CryptContext
from pathlib import Path
from urllib.parse import quote
from decimal import Decimal
from uuid import uuid4

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String(20), nullable=False, default="operator", server_default="operator")


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)

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
    status_changed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Status
    status = Column(Enum(OrderStatus), default=OrderStatus.IN_QUEUE)
    is_approved = Column(Boolean, default=False)
    needs_correction = Column(Boolean, default=False)

    operator = Column(String, nullable=True)

    # Relationship: One order can have multiple parts
    parts = relationship("OrderPart", back_populates="owner", cascade="all, delete-orphan", order_by="(OrderPart.position, OrderPart.id)")
    status_events = relationship("OrderStatusEvent", back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusEvent.id")

    @property
    def material_warnings(self):
        if self.status in (OrderStatus.COMPLETED, OrderStatus.ARCHIVED):
            return []
        warnings = []
        for part in self.parts:
            if not part.warehouse_material_id or part.planned_quantity is None:
                warnings.append(f'{part.part_name}: wybierz materiał z magazynu i podaj potrzebną ilość.')
            elif part.shortage_quantity > 0:
                warnings.append(f'{part.part_name}: brakuje {part.shortage_quantity.normalize():f} {part.planned_unit} '
                                f'materiału {part.planned_material_name}. Uzupełnij magazyn.')
            elif part.material_missing:
                warnings.append(f'{part.part_name}: zgłoszono brak materiału.')
        return warnings

    @property
    def materials_ready(self):
        return not self.material_warnings


class OrderPart(Base):
    __tablename__ = "order_parts"

    id = Column(Integer, primary_key=True, index=True)
    part_name = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    material = Column(String, nullable=True)
    material_missing = Column(Boolean, default=False)
    material_quantity = Column(Float, nullable=True)
    material_unit = Column(String, nullable=True)
    warehouse_material_id = Column(Integer, ForeignKey("warehouse_materials.id"), nullable=True)
    planned_quantity = Column(Numeric(18, 6), nullable=True)
    planned_for = Column(Date, nullable=True)
    reserved_quantity = Column(Numeric(18, 6), nullable=False, default=Decimal('0'), server_default='0')
    planned_material = relationship("WarehouseMaterial")

    @property
    def planned_unit(self):
        return self.planned_material.unit if self.planned_material else None

    @property
    def planned_material_name(self):
        return self.planned_material.name if self.planned_material else None

    @property
    def remaining_quantity(self):
        return max(Decimal('0'), (self.planned_quantity or Decimal('0')) - (self.issued_quantity or Decimal('0')))

    @property
    def shortage_quantity(self):
        if self.owner.status in (OrderStatus.COMPLETED, OrderStatus.ARCHIVED):
            return Decimal('0')
        return max(Decimal('0'), self.remaining_quantity - (self.reserved_quantity or Decimal('0')))
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


class WarehouseMaterial(Base):
    __tablename__ = "warehouse_materials"
    __table_args__ = (
        UniqueConstraint("name_key", "unit", name="uq_warehouse_material_unit"),
        CheckConstraint("quantity >= 0 AND quantity <= 999999999.999999", name="ck_warehouse_quantity"),
        CheckConstraint("minimum_quantity >= 0 AND minimum_quantity <= 999999999.999999", name="ck_warehouse_minimum"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    name_key = Column(String(512), nullable=False)
    unit = Column(String(20), nullable=False)
    quantity = Column(Numeric(18, 6), nullable=False, default=Decimal("0"))
    minimum_quantity = Column(Numeric(18, 6), nullable=False, default=Decimal("0"))

    @property
    def is_low_stock(self):
        return self.available_quantity <= self.minimum_quantity

    @property
    def available_quantity(self):
        return self.quantity - (self.reserved_quantity or Decimal('0'))


class WarehouseMovement(Base):
    __tablename__ = "warehouse_movements"
    __table_args__ = (
        CheckConstraint("quantity != 0", name="ck_warehouse_movement_nonzero"),
        CheckConstraint("balance_after >= 0", name="ck_warehouse_movement_balance"),
        CheckConstraint("kind IN ('opening', 'receipt', 'issue')", name="ck_warehouse_movement_kind"),
    )

    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("warehouse_materials.id"), nullable=False, index=True)
    order_part_id = Column(Integer, ForeignKey("order_parts.id", ondelete="SET NULL"), nullable=True, index=True)
    order_number = Column(String(255), nullable=False, default="", server_default="")
    part_name = Column(String(255), nullable=False, default="", server_default="")
    request_id = Column(String(100), nullable=False, unique=True, default=lambda: str(uuid4()))
    kind = Column(String(20), nullable=False)
    quantity = Column(Numeric(18, 6), nullable=False)
    balance_after = Column(Numeric(18, 6), nullable=False)
    note = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class WarehouseDelivery(Base):
    __tablename__ = "warehouse_deliveries"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity <= 999999999.999999", name="ck_delivery_quantity"),
        CheckConstraint("status IN ('ordered', 'received', 'cancelled')", name="ck_delivery_status"),
    )
    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("warehouse_materials.id"), nullable=False, index=True)
    quantity = Column(Numeric(18, 6), nullable=False)
    expected_on = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="ordered")
    note = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


OrderPart.issued_quantity = column_property(select(func.coalesce(-func.sum(WarehouseMovement.quantity), 0))
    .where(WarehouseMovement.order_part_id == OrderPart.id, WarehouseMovement.kind == 'issue')
    .correlate_except(WarehouseMovement).scalar_subquery())
WarehouseMaterial.reserved_quantity = column_property(select(func.coalesce(func.sum(OrderPart.reserved_quantity), 0))
    .where(OrderPart.warehouse_material_id == WarehouseMaterial.id)
    .correlate_except(OrderPart).scalar_subquery())
