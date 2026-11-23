# LabLink container for Google Cloud Run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY lablink ./lablink

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# One worker keeps in-process caches coherent; threads handle concurrency.
CMD exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads 8 --timeout 60 "lablink:create_app()"
