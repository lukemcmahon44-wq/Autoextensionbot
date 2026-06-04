FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal; wheels cover the Python libs.
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Run migrations, then serve webhooks + dashboard + (custom_claude) LLM websocket.
# For production set DATABASE_URL to Postgres; SQLite in a container is ephemeral.
CMD ["sh", "-c", "alembic upgrade head && uvicorn voiceagent.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
