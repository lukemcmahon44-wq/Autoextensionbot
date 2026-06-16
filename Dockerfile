# kalshi-scalper container image.
#
# Runtime secrets are NOT baked in: mount your .env and RSA key at run time, e.g.
#   docker run --rm \
#     -e FORCE_PAPER=true \
#     --env-file .env \
#     -v $PWD/kalshi_private_key.pem:/run/secrets/kalshi.pem:ro \
#     -e KALSHI_PRIVATE_KEY_PATH=/run/secrets/kalshi.pem \
#     kalshi-scalper
#
# The bot fails closed: without valid credentials a LIVE run refuses to start.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml ./config.yaml

# Run as an unprivileged user; give it a writable home for state.json.
RUN useradd --create-home --uid 10001 scalper && chown -R scalper:scalper /app
USER scalper

# Respects config.yaml (LIVE by default). Set FORCE_PAPER=true for a dry run.
CMD ["python", "-m", "src.main"]
