FROM python:3.12-slim
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt PyYAML
USER 65534:65534
