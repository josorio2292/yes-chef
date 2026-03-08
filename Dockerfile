FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for caching
COPY pyproject.toml uv.lock ./

# Install dependencies (without the project itself yet)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code and data
COPY src/ src/
COPY data/ data/
COPY alembic/ alembic/
COPY alembic.ini .
COPY entrypoint.sh .

# Install the project package itself
RUN uv pip install --no-deps -e .

# Expose port
EXPOSE 8000

# Run with entrypoint (runs migrations then starts uvicorn)
CMD ["./entrypoint.sh"]
