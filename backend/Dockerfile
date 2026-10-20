FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for PostgreSQL
RUN apt-get update && apt-get install -y \
    libpq-dev gcc \
    libpango-1.0-0 \
    libharfbuzz0b \
    libpangoft2-1.0-0 \
    libffi-dev \
    libjpeg-dev \
    libopenjp2-7-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY backend/requirements*.txt /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/requirements.txt

# Copy the rest of the application
COPY backend /app/backend
COPY frontend /app/frontend
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
