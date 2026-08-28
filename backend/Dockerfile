FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
CMD ["sh", "-c", "alembic -c alembic.ini upgrade head && python -m scripts.preflight && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
