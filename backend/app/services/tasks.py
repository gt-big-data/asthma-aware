"""Cloud Tasks transport: deterministic IDs, OIDC, bounded requests, no ML."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from google.api_core.exceptions import AlreadyExists


class TasksUnavailable(RuntimeError):
    pass


def task_config(settings):
    if not settings.tasks_enabled:
        raise TasksUnavailable("Personalization dispatch is disabled")
    url = urlparse(settings.personalization_worker_url)
    if url.scheme != "https" or not url.netloc or url.username is not None or url.password is not None or url.query or url.fragment or url.path not in ("", "/"):
        raise TasksUnavailable("Worker URL must be an HTTPS origin")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", settings.cloud_project):
        raise TasksUnavailable("Cloud project is not configured")
    for value in (settings.tasks_location, settings.tasks_queue):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
            raise TasksUnavailable("Task queue is not configured")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9-]+\.iam\.gserviceaccount\.com", settings.tasks_service_account_email):
        raise TasksUnavailable("Task service account is not configured")
    return f"projects/{settings.cloud_project}/locations/{settings.tasks_location}/queues/{settings.tasks_queue}", settings.personalization_worker_url.rstrip("/")


def enqueue(settings, record, client=None):
    parent, origin = task_config(settings)
    name = f"{parent}/tasks/personalization-{record['id']}"
    task = {"name": name, "dispatch_deadline": {"seconds": 60}, "http_request": {
        "http_method": 1, "url": origin + "/internal/tasks/personalization",
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"task_id": str(record["id"])}).encode(),
        "oidc_token": {"service_account_email": settings.tasks_service_account_email, "audience": origin},
    }}
    when = record.get("available_at")
    if when and when > datetime.now(timezone.utc):
        task["schedule_time"] = when
    owned = client is None
    try:
        if client is None:
            from google.cloud import tasks_v2
            client = tasks_v2.CloudTasksClient()
        client.create_task(request={"parent": parent, "task": task}, timeout=10, retry=None)
    except AlreadyExists:
        pass  # Deterministic name tolerates retries and ambiguous RPC outcomes.
    except Exception:
        raise TasksUnavailable("Task service is unavailable; retry the same idempotency key") from None
    finally:
        if owned and client is not None:
            client.transport.close()
    return name
