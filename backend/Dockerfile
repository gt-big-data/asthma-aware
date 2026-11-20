FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements.txt -r requirements/ingestion.txt
COPY app/ ./app/
COPY db/ ./db/
COPY ingestion/ ./ingestion/
COPY scripts/ ./scripts/
RUN useradd --create-home asthmaaware && mkdir /app/data && chown asthmaaware /app/data
USER asthmaaware
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
