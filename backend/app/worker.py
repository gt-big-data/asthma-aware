"""Private Cloud Run task handler. No ML implementation or fabricated output."""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from db.repositories import personalization as repository

bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger("asthmaaware.worker")
_cert_lock = threading.Lock()
_cert_response = None
_cert_at = 0.0


@dataclass(frozen=True)
class WorkerSettings:
    audience: str = ""
    service_account_email: str = ""

    @classmethod
    def from_env(cls):
        return cls(os.getenv("WORKER_AUDIENCE", "").strip().rstrip("/"), os.getenv("WORKER_SERVICE_ACCOUNT_EMAIL", "").strip())


def google_cert_request(url, method="GET", **kwargs):
    """Google's verified-token transport, bounded and cached public certificates."""
    global _cert_response, _cert_at
    from google.auth.transport.requests import Request
    import requests
    with _cert_lock:
        if _cert_response is not None and time.monotonic() - _cert_at < 300:
            return _cert_response
        with requests.Session() as session:
            response = Request(session=session)(url, method=method, timeout=5, **kwargs)
        if response.status != 200 or len(response.data) > 256 * 1024:
            from google.auth.exceptions import TransportError
            raise TransportError("Google signing keys unavailable")
        _cert_response, _cert_at = response, time.monotonic()
        return response


def verify_worker_token(token, settings):
    from google.oauth2.id_token import verify_oauth2_token
    from google.auth.exceptions import TransportError
    origin = urlparse(settings.audience)
    if origin.scheme != "https" or not origin.netloc or origin.username is not None or origin.password is not None or origin.query or origin.fragment or origin.path not in ("", "/") or not settings.service_account_email:
        raise HTTPException(503, "Worker identity is not configured")
    try:
        claims = verify_oauth2_token(token, google_cert_request, audience=settings.audience.rstrip("/"))
        if claims.get("email") != settings.service_account_email or claims.get("email_verified") is not True:
            raise ValueError("Unexpected service identity")
        return claims
    except TransportError:
        raise HTTPException(503, "Worker signing keys are temporarily unavailable") from None
    except Exception:
        raise HTTPException(401, "Invalid task identity") from None


class TaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID


def create_worker(settings=None):
    application = FastAPI(title="AsthmaAware Personalization Worker", docs_url=None, redoc_url=None, openapi_url=None)
    application.state.worker_settings = settings or WorkerSettings.from_env()

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Task bearer token required")
        return verify_worker_token(credentials.credentials, application.state.worker_settings)

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "personalization-worker"}

    @application.post("/internal/tasks/personalization")
    def handle(body: TaskBody, identity=Depends(authorize)):
        try:
            record = repository.handle_task(body.task_id)
        except repository.TaskNotFound:
            raise HTTPException(404, "Task not found") from None
        except repository.TaskConflict:
            raise HTTPException(503, "Task is not due; retry later") from None
        except Exception as exc:
            logger.warning(json.dumps({"event": "personalization_task_failed", "error_type": type(exc).__name__}))
            raise HTTPException(503, "Task database is unavailable") from None
        logger.info(json.dumps({"event": "personalization_task_handled", "status": record["status"],
                                "attempts": record["attempts"], "model_executed": False}))
        return {"task_id": str(record["id"]), "status": record["status"], "result": None,
                "reason": record["reason"]}

    return application


app = create_worker()
