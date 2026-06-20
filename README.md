# WeldERP System

**WeldERP System** is a simple ERP-style web application designed for managing production orders in a welding company. This project was created to streamline order registration, control the production flow, and efficiently track order statuses.

---

## Main Features

### Backend API (REST)
The backend handles business logic and database interactions, providing endpoints for:
- Creating new production orders
- Displaying a list of all orders
- Fetching details of a single order
- Updating the status of an order
- Deleting orders

### Frontend Interface
The web interface is user-friendly and allows operators to:
- Add new production orders via forms
- View all active and historical orders
- Update order statuses seamlessly
- Delete canceled or erroneous orders
- Refresh the order list in real-time

---

## Technologies Used

- **Backend:** Python 3.11, FastAPI, Uvicorn
- **Database:** PostgreSQL, SQLAlchemy (ORM)
- **Frontend:** HTML5, CSS3, JavaScript (Vanilla)
- **Infrastructure:** Docker, Docker Compose

---

## How to Run the Application

The project is fully containerized, making it incredibly easy to set up and run using Docker.

### Prerequisites
Make sure you have the following installed on your machine:
- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)
- [Git](https://git-scm.com/downloads)

### Step-by-Step Setup

**1. Clone the repository**
```bash
git clone https://github.com/bmszczesna/welderp-system.git
cd welderp-system
```

**2. Build and start the containers**

Run the following command in the root directory (where `docker-compose.yml` is located). This will pull the necessary images, build the backend, and start the database and web server.
```bash
docker compose up --build
```
*(Note: Use `docker-compose up --build` if you are on an older version of Docker Compose)*



**3. Stop the application**

To stop the application and shut down the containers, press `CTRL+C` in the terminal, or run:
```bash
docker compose down
```

---

## Application URLs

Once the containers are running, you can access the application via your browser:

- **Frontend Interface:** [http://localhost:8000](http://localhost:8000)
- **Interactive API Documentation (Swagger UI):** [http://localhost:8000/docs](http://localhost:8000/docs)


---

## System Logic

### Order Data Structure
Every order registered in the system contains the following key attributes:
- **Order Number** (Unique identifier)
- **Client Name**
- **Production Deadline**
- **Current Status**
- **List of Parts** (Items included in the order)

### Available Order Statuses
The production flow is controlled through the following statuses:
- `IN_QUEUE` – Waiting in the queue to be assigned.
- `IN_PROGRESS` – Currently in production/welding.
- `COMPLETED` – Finished and ready for delivery/quality check.

---

## Project Structure

```text
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
```