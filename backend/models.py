from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
import datetime


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
    status = Column(String, default="In queue")

    # Relationship: One order can have multiple parts
    parts = relationship("OrderPart", back_populates="owner", cascade="all, delete-orphan")


class OrderPart(Base):
    __tablename__ = "order_parts"

    id = Column(Integer, primary_key=True, index=True)
    part_name = Column(String, nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"))

    owner = relationship("Order", back_populates="parts")