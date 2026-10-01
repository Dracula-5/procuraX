# Production image for the ProcuraX API (multi-stage: build tools stay out of the runtime).

FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
# Dependencies first: this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --python-preference only-system

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PROCURAX_ENV=prod
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# Local OCR for the invoice extraction preview: Tesseract (English + Japanese) and Poppler.
RUN apt-get update  && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-jpn poppler-utils  && rm -rf /var/lib/apt/lists/*
# Code stays root-owned and read-only to the runtime user.
RUN useradd --system --uid 10001 --no-create-home procurax
USER procurax

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health').status == 200 else 1)"

# Migrations run as a separate release step (`alembic upgrade head` with the owner URL),
# never implicitly on web-process start — see docs/system_design.md.
# Hosting platforms (Render, Cloud Run, ...) inject PORT; locally it defaults to 8000.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
