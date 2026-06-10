FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/amanya/potcast" \
    org.opencontainers.image.description="Personal podcast radio service"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg mpv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY potcast ./potcast

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

EXPOSE 8080

CMD ["potcast", "--config", "/config/potcast.yaml"]
