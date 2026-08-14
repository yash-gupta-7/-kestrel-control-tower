FROM python:3.11-slim

WORKDIR /app
COPY etl/requirements.txt ./etl/requirements.txt
RUN pip install --no-cache-dir -r etl/requirements.txt
COPY etl/ ./etl/

# Runs the full pipeline once, in order, and exits. docker-compose.yml
# gates the backend on this container exiting 0
# (condition: service_completed_successfully).
CMD python3 etl/scrape_bazaarpulse.py && \
    python3 etl/pull_freight_invoices.py && \
    python3 etl/pull_weather.py && \
    python3 etl/build_warehouse.py && \
    python3 etl/validate_warehouse.py
