# Container image for the intraday paper-trading service.
# Build from the repo root:
#   docker build -f deploy/Dockerfile -t stock-trader .
# Run (dry-run, paper):
#   docker run --rm --env-file .env stock-trader
FROM python:3.13-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make the package importable without the .venv path shims.
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Safe default: dry-run. Override the command to add --submit on a paper account.
ENTRYPOINT ["python", "scripts/live_trader.py"]
CMD ["--universe", "starter"]
