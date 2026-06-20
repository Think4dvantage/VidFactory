FROM python:3.11-slim

# FFmpeg (ships libx264 + h264_nvenc + h264_qsv encoders), Intel VAAPI driver for QSV,
# and Liberation fonts for the CTA drawtext. NVENC works at runtime via the NVIDIA Container Toolkit.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    va-driver-all \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY src/ ./src/
COPY templates/ ./templates/
COPY static/ ./static/
COPY resources/ ./resources/
RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "vidfactory.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
