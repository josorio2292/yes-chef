#!/bin/bash
set -e

# Run migrations
uv run alembic upgrade head

# Start the API
exec uv run uvicorn yes_chef.api.app:app --host 0.0.0.0 --port 8000
