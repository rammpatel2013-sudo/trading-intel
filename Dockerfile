FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps for psycopg, pdfplumber (poppler), etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    poppler-utils \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better Docker layer caching)
COPY pyproject.toml ./
RUN pip install -e .

# Headless browser for report PNG previews (swing dossier -> Telegram image).
RUN pip install playwright && python -m playwright install --with-deps chromium

# Copy app
COPY trading_intel ./trading_intel
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./

# No default service: every NAS container is started with an explicit command (DSM tasks, scripts/nas/run_*.sh).
# 2026-11-14 (O4): the Streamlit dashboard (old default CMD, port 8501) was archived -> archive/trading_intel/dashboard/.
# A bare `docker run --rm trading-intel` now runs the image smoke check.
CMD ["python", "scripts/nas/image_smoke.py"]
