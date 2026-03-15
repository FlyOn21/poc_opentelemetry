FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
  && rm -rf /var/lib/apt/lists/* \
  && pip install -U pip && pip install uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY ../pyproject.toml uv.lock* ./
RUN uv venv && uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

COPY fastapi-app /app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
