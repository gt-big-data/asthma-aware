FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /job
# This milestone needs only Python's standard library. No pip install required.
COPY --chown=10001:10001 . /job/forecast_job/
USER 10001:10001
ENTRYPOINT ["python", "-m", "forecast_job"]
CMD ["--mode", "sample", "--output-dir", "/tmp/forecast"]
