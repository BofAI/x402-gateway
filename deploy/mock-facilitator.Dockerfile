FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN pip install --upgrade pip \
    && pip install fastapi uvicorn

COPY e2e/mock_facilitator/ ./e2e/mock_facilitator/

EXPOSE 4021

CMD ["python", "-m", "e2e.mock_facilitator", "--host", "0.0.0.0", "--port", "4021"]
