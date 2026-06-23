FROM python:3.11-slim

WORKDIR /app

COPY transform_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY transform_service/ transform_service/
COPY scripts/ scripts/

CMD ["uvicorn", "transform_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/transform_service"]
