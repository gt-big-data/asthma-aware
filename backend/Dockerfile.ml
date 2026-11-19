# Build from the repository root: docker build -f backend/Dockerfile.ml .
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MODEL_MANIFEST_PATH=/app/model-manifest.json
WORKDIR /app
COPY backend/requirements.txt ./
COPY backend/requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements.txt -r requirements/ingestion.txt \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.6,<3"
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY ml-work/pyproject.toml /opt/ml-work/pyproject.toml
COPY ml-work/asthma_aware/ /opt/ml-work/asthma_aware/
RUN pip install --no-cache-dir --no-deps /opt/ml-work
COPY backend/app/ ./app/
COPY backend/db/ ./db/
COPY backend/ingestion/ ./ingestion/
COPY backend/scripts/ ./scripts/
COPY models/checkpoints/manifest.json ./model-manifest.json
RUN useradd --create-home asthmaaware && mkdir /app/data && chown asthmaaware /app/data
USER asthmaaware
ENTRYPOINT ["python", "-m", "scripts.publish_dashboard_forecast"]
CMD ["--model-dir", "data/models"]
