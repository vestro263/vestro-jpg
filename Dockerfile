FROM python:3.11-slim

WORKDIR /app

COPY vestro_backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vestro_backend ./vestro_backend

ENV PYTHONPATH=/app/vestro_backend

EXPOSE 10000

CMD ["uvicorn", "vestro_backend.app.main:app", "--host", "0.0.0.0", "--port", "10000"]