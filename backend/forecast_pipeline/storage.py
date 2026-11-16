"""Bounded binary artifact storage for GCS or an isolated local SQLite catalog."""
from pathlib import Path
from contextlib import contextmanager
import sqlite3

from forecast_job.storage import Conflict, Object, GCSStore
from forecast_pipeline.contracts import MAX_OBJECT_BYTES, object_name


class LocalArtifactStore:
    """Transactional local object versions; no cloud clients or credentials."""
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "artifacts.sqlite3"
        with self._connect() as conn:
            conn.executescript("""CREATE TABLE IF NOT EXISTS objects (
                generation INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, data BLOB NOT NULL, content_type TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS objects_name ON objects(name, generation DESC);""")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def read(self, name, generation=None):
        object_name(name)
        with self._connect() as conn:
            if generation is None:
                row = conn.execute("SELECT data,generation FROM objects WHERE name=? ORDER BY generation DESC LIMIT 1", (name,)).fetchone()
            else:
                row = conn.execute("SELECT data,generation FROM objects WHERE name=? AND generation=?", (name, generation)).fetchone()
        if row is None:
            raise KeyError(name)
        return Object(bytes(row[0]), int(row[1]))

    def write(self, name, data, expected=0, content_type="application/json"):
        object_name(name)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_OBJECT_BYTES:
            raise ValueError("Artifact size/type is invalid")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT generation FROM objects WHERE name=? ORDER BY generation DESC LIMIT 1", (name,)).fetchone()
            current = int(row[0]) if row else 0
            if current != expected:
                raise Conflict(name)
            cursor = conn.execute("INSERT INTO objects(name,data,content_type) VALUES(?,?,?)", (name, data, content_type))
            return Object(data, cursor.lastrowid)


class GCSArtifactStore(GCSStore):
    """Version-pinned reads and binary MIME metadata with conditional writes."""
    def read(self, name, generation=None):
        object_name(name)
        from google.api_core.exceptions import NotFound
        blob = self.bucket.blob(name, generation=generation)
        try:
            blob.reload(timeout=5, retry=self.retry)
            if blob.size is None or blob.size > MAX_OBJECT_BYTES:
                raise ValueError("Cloud artifact exceeds the bounded read size")
            pinned = self.bucket.blob(name, generation=int(blob.generation))
            return Object(pinned.download_as_bytes(timeout=10, retry=self.retry), int(blob.generation))
        except NotFound as exc:
            raise KeyError(name) from exc

    def write(self, name, data, expected=0, content_type="application/json"):
        object_name(name)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_OBJECT_BYTES:
            raise ValueError("Artifact size/type is invalid")
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(name)
        blob.cache_control = "no-store" if name.endswith("current.json") or name.startswith("raw/") else "public,max-age=86400,immutable"
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=expected, timeout=20, retry=self.retry)
        except PreconditionFailed as exc:
            raise Conflict(name) from exc
        return Object(data, int(blob.generation))
