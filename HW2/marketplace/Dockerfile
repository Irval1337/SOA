FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY openapi ./openapi
COPY codegen ./codegen
RUN ./codegen/generate.sh

FROM base AS runtime
COPY app ./app
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=1s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1
CMD ["python", "-m", "app"]

FROM runtime AS test
COPY pyproject.toml .
COPY tests ./tests
CMD ["pytest", "-p", "no:cacheprovider"]
