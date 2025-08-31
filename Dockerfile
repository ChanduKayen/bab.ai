# ---- Base Python image ----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system deps (build tools only for wheels), curl for CA download
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* 

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the RDS us-east-1 CA bundle into /certs
RUN mkdir -p /certs && \
    curl -fsSL --retry 3 \
      https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem \
      -o /certs/rds-us-east-1-bundle.pem

# (Optional) drop build tools to slim the image
RUN apt-get purge -y build-essential && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Copy app code
COPY . .

# Non-root user
RUN useradd -ms /bin/bash appuser && chown -R appuser:appuser /app /certs
USER appuser

# App defaults (App Runner can override)
ENV PORT=8000
ENV APP_MODULE=app.main:app
# Helpful defaults for Path A (override in App Runner as needed)
# ENV DB_SSLMODE=verify-full
# ENV DB_SSLROOTCERT=/certs/rds-us-east-1-bundle.pem

# Start the server
CMD exec gunicorn -k uvicorn.workers.UvicornWorker -w 2 \
    -b 0.0.0.0:${PORT} ${APP_MODULE}
