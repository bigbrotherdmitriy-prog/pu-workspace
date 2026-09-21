FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/app /app/app
RUN useradd --system --uid 10001 --create-home bridge \
    && mkdir -p /data \
    && chown bridge:bridge /data
USER bridge
EXPOSE 8080
CMD ["uvicorn", "app.v54_live_provider_bridge:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
