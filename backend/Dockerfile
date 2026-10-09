FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer stays cached until requirements.txt changes.
# All packages ship manylinux wheels (psycopg2-binary included): no apt-get needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user.
RUN adduser --disabled-password --gecos "" --uid 1000 appuser
COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000

# Production-like default; docker-compose overrides it with --reload for development.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
