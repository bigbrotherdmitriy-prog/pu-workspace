ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER root
WORKDIR /app
COPY app ./app
RUN mkdir -p /app/data && (id -u bot >/dev/null 2>&1 || useradd --system --uid 10001 bot) && chown -R bot:bot /app
USER bot
CMD ["python", "-m", "app.main"]
