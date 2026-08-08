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

# Expose Streamlit
EXPOSE 8501

# Default command (overridden by docker-compose for scheduler)
CMD ["streamlit", "run", "trading_intel/dashboard/Home.py", "--server.port=8501", "--server.address=0.0.0.0"]
