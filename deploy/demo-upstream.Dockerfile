FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install --upgrade pip \
    && pip install fastapi uvicorn

COPY demo/upstream_server.py ./demo/upstream_server.py

EXPOSE 8080

CMD ["uvicorn", "demo.upstream_server:app", "--host", "0.0.0.0", "--port", "8080"]
