FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PostgreSQL
RUN apt-get update && apt-get install -y libpq-dev gcc

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .