"""Opt-in Places Text Search with bounded fields/time/cost and no content cache."""
import math
import threading
import time

import httpx


class PlacesUnavailable(RuntimeError):
    pass


class PlacesRateLimited(PlacesUnavailable):
    pass


class RequestLimiter:
    """Per-instance safety limit, not a distributed billing cap. Max one API instance initially."""
    def __init__(self, per_user=5, total=30, clock=time.monotonic):
        self.per_user, self.total, self.clock = per_user, total, clock
        self.lock = threading.Lock()
        self.buckets = {}
        self.global_bucket = []

    def allow(self, user_id, per_user=None):
        now = self.clock()
        with self.lock:
            self.buckets = {key: [t for t in times if now-t<60] for key, times in self.buckets.items() if times and now-times[-1]<60}
            self.global_bucket = [t for t in self.global_bucket if now-t<60]
            times = self.buckets.get(str(user_id), [])
            per_user = min(self.per_user, max(1, int(per_user or self.per_user)))
            if len(times) >= per_user or len(self.global_bucket) >= self.total or len(self.buckets) >= 10000:
                raise PlacesRateLimited("Places request limit reached; try again later")
            self.buckets[str(user_id)] = times + [now]
            self.global_bucket.append(now)


limiter = RequestLimiter()


def search(settings, query, *, latitude=None, longitude=None, client=None):
    if not settings.places_enabled or not settings.places_api_key:
        raise PlacesUnavailable("Places integration is disabled")
    body = {"textQuery": query, "pageSize": 5, "languageCode": "en"}
    if latitude is not None and longitude is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": latitude, "longitude": longitude}, "radius": 50000.0}}
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(5), follow_redirects=False)
    try:
        response = client.post("https://places.googleapis.com/v1/places:searchText", json=body,
            headers={"X-Goog-Api-Key": settings.places_api_key,
                     "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.attributions"}, timeout=5)
        if response.status_code == 429:
            raise PlacesRateLimited("Places provider rate limit reached")
        if response.status_code != 200 or len(response.content) > 256 * 1024:
            raise PlacesUnavailable("Places provider is unavailable")
        payload = response.json()
        results = []
        for place in payload.get("places", [])[:5]:
            lat, lon = place["location"]["latitude"], place["location"]["longitude"]
            if type(lat) not in (int,float) or type(lon) not in (int,float) or not math.isfinite(lat) or not math.isfinite(lon) or abs(lat)>90 or abs(lon)>180:
                raise ValueError("Invalid Places coordinates")
            place_id = place["id"]
            name = place["displayName"]["text"]
            if not isinstance(place_id, str) or not isinstance(name, str):
                raise ValueError("Invalid place result")
            results.append({"place_id": place_id, "name": name, "latitude": lat, "longitude": lon,
                            "attributions": place.get("attributions", [])})
        return {"places": results, "provider": "Google Places", "attribution_required": True}
    except PlacesUnavailable:
        raise
    except Exception:
        raise PlacesUnavailable("Places provider response is unavailable or invalid") from None
    finally:
        if owned:
            client.close()
