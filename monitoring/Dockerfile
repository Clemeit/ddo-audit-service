# FROM python:3.11-slim
FROM python@sha256:02ca4ef9d27dbccfc921dda5a135434c64bcea9d2691d35c9a22eefb304df71b

WORKDIR /app

# Install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the monitoring service
CMD ["python", "monitor.py"]
