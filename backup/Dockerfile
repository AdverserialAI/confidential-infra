FROM python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

LABEL org.opencontainers.image.source="https://github.com/AdverserialAI/confidential-infra" \
      org.opencontainers.image.description="One-shot private CyberGLM artifact backup uploader"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN mkdir -p /app /state \
    && chown -R 65532:65532 /app /state

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY backup.py /app/backup.py

USER 65532:65532
ENTRYPOINT ["python", "/app/backup.py"]
