FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg (PostgreSQL) binary wheels
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cache-friendly layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source and config
COPY pyproject.toml alembic.ini langgraph.json ./
COPY src/ src/

# Install the project in editable mode so agents/shared packages are importable
RUN pip install --no-cache-dir -e .

# Copy sample data (pre-generated, committed to repo)
COPY data/ data/

# Streamlit config: disable telemetry, set server defaults
RUN mkdir -p /root/.streamlit && \
    printf '[browser]\ngatherUsageStats = false\n[server]\nheadless = true\naddress = "0.0.0.0"\nport = 8501\nenableCORS = false\n' \
    > /root/.streamlit/config.toml

# Default: Streamlit. Overridden by compose for the studio service.
CMD ["streamlit", "run", "src/ui/app.py"]
