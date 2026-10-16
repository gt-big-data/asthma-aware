"""Where synced wearable samples live.

A sample is identified by ``(user_id, metric, date)``, and writing one
replaces whatever was there. That makes a sync **idempotent**: a watch
that uploads the same night twice -- because the app was reopened, or the
first response was lost on a train -- ends up with one row, not two. It
also lets a device correct itself, which they do: overnight metrics are
often revised a few hours later once the full sleep stage analysis
finishes, and the correction simply overwrites the first guess.

Storage is a JSON file, read once into memory and rewritten on every
accepted sync. That is the right size for this stage of the project: the
read path is pure dictionary lookups, there is nothing to install, and a
teammate can open the file and see exactly what the watch sent. The real
home for this data is Postgres -- ``backend/db/`` already has the
migrations, session handling and repository pattern for it, and the
natural schema is one ``wearable_observations`` table keyed by
``(user_id, variable_id, observation_date)``, mirroring
``grid_observations``. Everything outside this module works in terms of
the four functions at the bottom of it, so that swap does not reach the
services.

Concurrency
-----------
Uvicorn runs sync endpoint functions in a thread pool, so two syncs can
land at once. All access is taken under one lock, and the file is written
by writing a temporary file and renaming it over the original, which is
atomic on POSIX. A crash mid-write therefore leaves the previous file
intact rather than a half-written one.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple

from app.services import demo_history

# Overridable so tests and parallel demos do not share one file.
STORE_PATH_ENV_VAR = "WEARABLE_STORE_PATH"
DEFAULT_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "wearable_store.json"

STORE_FORMAT_VERSION = 1


def _store_path() -> Optional[Path]:
    """The file to persist to. ``:memory:`` disables persistence entirely."""
    configured = os.environ.get(STORE_PATH_ENV_VAR)
    if configured is None:
        return DEFAULT_STORE_PATH
    if configured == ":memory:":
        return None
    return Path(configured)


class SampleStore:
    """Daily wearable samples for every user, plus each user's device state."""

    def __init__(self, path: Optional[Path] = None, seed_demo_user: bool = True):
        self._path = path
        self._seed_demo_user = seed_demo_user
        self._lock = threading.RLock()
        # user_id -> metric_slug -> date -> value
        self._samples: Dict[str, Dict[str, Dict[dt.date, float]]] = {}
        # user_id -> {"device_id": str | None, "last_sync_at": datetime | None}
        self._devices: Dict[str, Dict[str, object]] = {}
        self._loaded = False

    # --- loading and persistence -----------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self._path is not None and self._path.exists():
                self._read_file(self._path)
            elif self._seed_demo_user:
                self._seed()
                self._write_file()
            self._loaded = True

    def _seed(self) -> None:
        """Populate the demo user so the read endpoints are never empty."""
        today = dt.date.today()
        self._samples[demo_history.DEMO_USER_ID] = demo_history.generate_history(today)
        self._devices[demo_history.DEMO_USER_ID] = {
            "device_id": demo_history.DEMO_DEVICE_ID,
            # The watch would have synced this morning.
            "last_sync_at": dt.datetime.combine(
                today, dt.time(7, 30), tzinfo=dt.timezone.utc
            ),
        }

    def _read_file(self, path: Path) -> None:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)

        for user_id, user in payload.get("users", {}).items():
            self._samples[user_id] = {
                metric: {
                    dt.date.fromisoformat(date): float(value)
                    for date, value in series.items()
                }
                for metric, series in user.get("metrics", {}).items()
            }
            device = user.get("device") or {}
            last_sync = device.get("last_sync_at")
            self._devices[user_id] = {
                "device_id": device.get("device_id"),
                "last_sync_at": (
                    None if last_sync is None else dt.datetime.fromisoformat(last_sync)
                ),
            }

    def _write_file(self) -> None:
        if self._path is None:
            return

        payload = {
            "_comment": (
                "Synced wearable samples, written by backend/wearable. Delete this "
                "file to reset the store and regenerate the demo user's history."
            ),
            "version": STORE_FORMAT_VERSION,
            "users": {
                user_id: {
                    "device": {
                        "device_id": self._devices.get(user_id, {}).get("device_id"),
                        "last_sync_at": _iso_or_none(
                            self._devices.get(user_id, {}).get("last_sync_at")
                        ),
                    },
                    "metrics": {
                        metric: {
                            date.isoformat(): value for date, value in sorted(series.items())
                        }
                        for metric, series in sorted(metrics.items())
                    },
                }
                for user_id, metrics in sorted(self._samples.items())
            },
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: an interrupted write cannot corrupt the store.
        # The temporary file is created 0600 and os.replace preserves that,
        # which is what we want for a file of someone's health history.
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self._path.parent,
            prefix=self._path.name,
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(payload, handle, indent=2, sort_keys=False)
                handle.write("\n")
            os.replace(handle.name, self._path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

    # --- reads ------------------------------------------------------------

    def history(self, user_id: str) -> Dict[str, Dict[dt.date, float]]:
        """Every metric's full series for one user.

        A copy, so callers cannot mutate the store by accident.
        """
        self._ensure_loaded()
        with self._lock:
            return {
                metric: dict(series)
                for metric, series in self._samples.get(user_id, {}).items()
            }

    def device_state(self, user_id: str) -> Dict[str, object]:
        """The user's last-seen device id and sync time."""
        self._ensure_loaded()
        with self._lock:
            state = self._devices.get(user_id, {})
            return {
                "device_id": state.get("device_id"),
                "last_sync_at": state.get("last_sync_at"),
            }

    def known_users(self) -> Tuple[str, ...]:
        self._ensure_loaded()
        with self._lock:
            return tuple(sorted(self._samples))

    # --- writes -----------------------------------------------------------

    def upsert_samples(
        self,
        user_id: str,
        samples: Iterable[Tuple[str, dt.date, float]],
        device_id: Optional[str] = None,
        synced_at: Optional[dt.datetime] = None,
    ) -> int:
        """Store ``(metric, date, value)`` triples, replacing any existing ones.

        Callers validate first (see ``ingest_service``); this writes what
        it is given. Returns how many samples were stored. Within one
        call, a repeated ``(metric, date)`` keeps the last value, matching
        the overwrite rule.
        """
        samples = list(samples)
        self._ensure_loaded()

        with self._lock:
            user_samples = self._samples.setdefault(user_id, {})
            for metric_slug, date, value in samples:
                user_samples.setdefault(metric_slug, {})[date] = float(value)

            device = self._devices.setdefault(
                user_id, {"device_id": None, "last_sync_at": None}
            )
            if device_id is not None:
                device["device_id"] = device_id
            device["last_sync_at"] = synced_at or dt.datetime.now(dt.timezone.utc)

            # Always persist, even for a sync whose every sample was
            # rejected: the device did check in, and last_sync_at is how
            # the app knows the watch is still reporting at all. Skipping
            # the write here would leave that only in memory, so a restart
            # would lose it.
            self._write_file()

        return len(samples)


def _iso_or_none(moment: object) -> Optional[str]:
    if not isinstance(moment, dt.datetime):
        return None
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- module-level singleton ----------------------------------------------
#
# One store per process, created on first use so that importing this module
# never touches the filesystem (which would make test setup order matter).

_store: Optional[SampleStore] = None
_store_lock = threading.Lock()


def get_store() -> SampleStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SampleStore(path=_store_path())
    return _store


def reset_store(
    path: Optional[Path] = None,
    history: Optional[Mapping[str, Mapping[str, Mapping[dt.date, float]]]] = None,
    seed_demo_user: bool = False,
) -> SampleStore:
    """Replace the process store. For tests and local resets.

    With ``history`` (``{user_id: {metric: {date: value}}}``) the store
    starts from exactly that data and never reads or writes a file.
    """
    global _store
    with _store_lock:
        if history is not None:
            store = SampleStore(path=None, seed_demo_user=False)
            store._samples = {
                user_id: {
                    metric: dict(series) for metric, series in metrics.items()
                }
                for user_id, metrics in history.items()
            }
            store._devices = {
                user_id: {"device_id": "test-device", "last_sync_at": None}
                for user_id in history
            }
            store._loaded = True
        else:
            store = SampleStore(path=path, seed_demo_user=seed_demo_user)
        _store = store
        return store
