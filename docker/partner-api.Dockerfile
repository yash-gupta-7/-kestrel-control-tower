# Wraps the assignment pack's own partner_api/server.py (mounted at
# runtime from ASSIGNMENT_PACK_DIR -- that code isn't ours to copy into
# a committed image, see docker-compose.yml). This Dockerfile only
# provides its two dependencies.
FROM python:3.11-slim
RUN pip install --no-cache-dir fastapi uvicorn
WORKDIR /app
EXPOSE 8088
CMD ["python3", "server.py"]
