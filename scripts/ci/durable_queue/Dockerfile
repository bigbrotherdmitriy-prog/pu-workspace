ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY scripts/ci/durable_queue /queue_ci
COPY scripts/ci/smoke_api.py /queue_ci/smoke_api.py
COPY scripts/backup-job-queue.sh scripts/restore-job-queue.sh /queue_tools/
ENV PYTHONPATH=/app:/queue_ci
