# ---- Base Python image ----
FROM python:3.11-slim

# Fast, reliable Python in containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# (Optional) common build deps; keep slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Workdir inside the image
WORKDIR /app

# Install deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code
COPY . .

# App Runner will set $PORT; default to 8000 for local
ENV PORT=8000

# IMPORTANT: path to your FastAPI app object. Change if needed.
# Example alternatives:
#   backend.app.main:app
#   src.api.main:app
ENV APP_MODULE=app.main:app

# Start with gunicorn + uvicorn workers
CMD exec gunicorn -k uvicorn.workers.UvicornWorker -w 2 \
    -b 0.0.0.0:${PORT} ${APP_MODULE}
