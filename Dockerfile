# Use official Python 3.11 image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Expose the port Render assigns
EXPOSE 10000

# Start FastAPI via Uvicorn
CMD ["uvicorn", "vestro_backend.app.main:app", "--host", "0.0.0.0", "--port", "10000"]