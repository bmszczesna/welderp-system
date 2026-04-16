# WeldERP System

WeldERP System is a simple ERP-style web application for managing production orders in a welding company.

The project was created to support order registration, production flow control, and order status tracking.

---

# Technologies Used

- Python 3.11
- FastAPI
- PostgreSQL
- SQLAlchemy
- Docker
- Docker Compose
- HTML
- CSS
- JavaScript

---

# Main Features

## Backend API

The application provides REST API endpoints for:

- creating new orders
- displaying all orders
- displaying a single order
- updating order status
- deleting orders

## Frontend Interface

The web interface allows users to:

- add new production orders
- view all orders
- update order status
- delete orders
- refresh the order list

---

# Order Data Structure

Each order contains:

- order number
- client name
- production deadline
- current status
- list of parts

---

# Available Order Statuses

- `IN_QUEUE` – waiting in queue
- `IN_PROGRESS` – currently in production
- `COMPLETED` – finished

---

# Application URLs

## Frontend

http://localhost:8000

## API Documentation (Swagger UI)

http://localhost:8000/docs

---

# Project Structure

```text id="kg2l03"
welderp-system/
│
├── backend/
│   ├── main.py
│   ├── models.py
│   ├── database.py
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   └── index.html
│
├── tests/
│
├── docker-compose.yml
└── README.md
