FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY studio studio

ENV STUDIO_SCRATCH=/tmp/desk
EXPOSE 8000
CMD ["sh", "-c", "uvicorn studio.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
