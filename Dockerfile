# tamperlint HTTP API
#   docker build -t tamperlint .
#   docker run --rm -p 8000:8000 tamperlint
#   curl -F file=@statement.pdf http://localhost:8000/v1/check
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[server]" && useradd --create-home --uid 10001 tamperlint

USER tamperlint
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "tamperlint.server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
