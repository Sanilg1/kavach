# Kavach backend: FastAPI + background worker + FFmpeg + handwriting font.
# Deploy to AWS App Runner / ECS Fargate (recommended) or as a Lambda container image (handler = app.main.handler).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY assets ./assets
COPY scripts ./scripts

# Optional handwriting font (Patrick Hand, OFL). Falls back to DejaVu if the download fails.
RUN mkdir -p assets/fonts && curl -fsSL -o assets/fonts/PatrickHand-Regular.ttf \
    https://github.com/google/fonts/raw/main/ofl/patrickhand/PatrickHand-Regular.ttf || true

ENV KAVACH_MODE=aws KAVACH_DATA_DIR=/tmp/kavach KAVACH_WORK_DIR=/tmp/kavach/work PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
