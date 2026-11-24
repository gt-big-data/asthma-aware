"""Generation-checked, expiring publication lease shared by publisher executions."""
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from google.api_core.exceptions import NotFound, PreconditionFailed

KEY = "control/forecast-publication-lease.json"
logger = logging.getLogger(__name__)


def _read(bucket):
    blob = bucket.get_blob(KEY, timeout=10)
    if blob is None:
        return None, 0
    generation = int(blob.generation)
    try:
        data = json.loads(blob.download_as_bytes(if_generation_match=generation, timeout=10))
    except NotFound:
        return None, 0
    return data, generation


def lease_active(bucket, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        data, _ = _read(bucket)
        return data is not None and datetime.fromisoformat(data["expires_at"]) > now
    except PreconditionFailed:
        return True


@contextmanager
def publication_lease(bucket, *, now=None):
    # Job timeout is 60 minutes. A killed container cannot hold this indefinitely.
    now = now or datetime.now(timezone.utc)
    owner = str(uuid4())
    acquired = None
    for _ in range(3):
        try:
            old, generation = _read(bucket)
            if old is not None and datetime.fromisoformat(old["expires_at"]) > now:
                break
            blob = bucket.blob(KEY)
            blob.upload_from_string(json.dumps({"owner": owner,
                "expires_at": (now + timedelta(minutes=75)).isoformat()}),
                content_type="application/json", if_generation_match=generation, timeout=10)
            acquired = int(blob.generation)
            break
        except PreconditionFailed:
            continue
    try:
        yield acquired is not None
    finally:
        if acquired is not None:
            try:
                bucket.blob(KEY).delete(if_generation_match=acquired, timeout=10)
            except (NotFound, PreconditionFailed):
                pass
            except Exception:
                logger.exception("Could not release publisher lease; it will expire automatically")
