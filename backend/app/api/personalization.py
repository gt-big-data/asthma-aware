"""Verified-user task submission/status; queue payload is an opaque task ID."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import get_current_user_id
from app.services.tasks import TasksUnavailable, enqueue, task_config
from db.repositories import personalization as repository

router = APIRouter(prefix="/v1/personalization/tasks", tags=["Personalization"])


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_id: UUID
    log_revision: int = Field(strict=True, ge=1)
    idempotency_key: UUID


def render(record):
    return {key: record[key] for key in ("id", "log_id", "log_revision", "status", "reason", "attempts", "available_at", "created_at", "updated_at")}


@router.post("", status_code=202)
def submit(body: TaskRequest, request: Request, response: Response, user_id: UUID = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        task_config(request.app.state.settings)  # Disabled mode does not write DB.
        record = repository.request_task(user_id, body.log_id, body.log_revision, body.idempotency_key)
        if record["status"] == "pending_dispatch":
            name = enqueue(request.app.state.settings, record)
            record = repository.mark_queued(user_id, record["id"], name)
        return render(record)
    except repository.TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except repository.TaskConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except TasksUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except Exception:
        raise HTTPException(503, "Task database is unavailable") from None


@router.get("/{task_id}")
def status(task_id: UUID, response: Response, user_id: UUID = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return render(repository.get_task(user_id, task_id))
    except repository.TaskNotFound:
        raise HTTPException(404, "Task not found") from None
    except Exception:
        raise HTTPException(503, "Task database is unavailable") from None
