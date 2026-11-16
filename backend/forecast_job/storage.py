"""Generation-aware storage. Google clients are imported only in cloud mode."""

from dataclasses import dataclass


class Conflict(Exception):
    """A conditional write lost a race or attempted to replace an existing run."""


@dataclass(frozen=True)
class Object:
    data: bytes
    generation: int


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.versions = {}
        self.sequence = 0

    def read(self, name, generation=None):
        if generation is not None:
            return self.versions[(name, generation)]
        return self.objects[name]

    def write(self, name, data, expected=0):
        current = self.objects.get(name)
        if (current.generation if current else 0) != expected:
            raise Conflict(name)
        self.sequence += 1
        obj = Object(data, self.sequence)
        self.objects[name] = obj
        self.versions[(name, obj.generation)] = obj
        return obj


class GCSStore:
    def __init__(self, bucket):
        if not bucket or 'REPLACE' in bucket:
            raise ValueError('Set a real FORECAST_BUCKET for GCS mode')
        from google.cloud import storage
        from google.api_core.retry import Retry
        self.bucket = storage.Client().bucket(bucket)
        self.retry = Retry(deadline=10)

    def read(self, name, generation=None):
        from google.api_core.exceptions import NotFound
        blob = self.bucket.blob(name, generation=generation)
        try:
            if generation is None:
                blob.reload(timeout=5, retry=self.retry)
            pinned = self.bucket.blob(name, generation=int(blob.generation))
            return Object(pinned.download_as_bytes(timeout=5, retry=self.retry), int(blob.generation))
        except NotFound as exc:
            raise KeyError(name) from exc

    def write(self, name, data, expected=0):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type='application/json',
                                    if_generation_match=expected, timeout=5, retry=self.retry)
        except PreconditionFailed as exc:
            raise Conflict(name) from exc
        return Object(data, int(blob.generation))
