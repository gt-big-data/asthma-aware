"""Read-only checks of the hosted web, forecast, map and authentication boundary."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

API = "https://asthmaaware-api-243825508967.us-east1.run.app"
WEB = "https://asthmaaware-web-243825508967.us-east1.run.app"


def get(base, path, headers=None, **params):
    query = "?" + urllib.parse.urlencode(params) if params else ""
    request = urllib.request.Request(base + path + query, headers=headers or {})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def check_authenticated_reads():
    from dotenv import dotenv_values
    root = Path(__file__).resolve().parents[2]
    private = dotenv_values(root / "backend/.env")
    public = dotenv_values(root / "Front-end/.env.local")
    supabase = "https://robbrnvlnysxeqvgaupt.supabase.co"
    headers = {"apikey": public["EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY"],
               "Content-Type": "application/json"}
    request = urllib.request.Request(supabase + "/auth/v1/token?grant_type=password",
        headers=headers, data=json.dumps({"email": private["SMOKE_TEST_EMAIL"],
        "password": private["SMOKE_TEST_PASSWORD"]}).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)["access_token"]
    authenticated = {"Authorization": f"Bearer {token}"}
    try:
        for path in ("/v1/users/me", "/v1/symptoms", "/v1/locations"):
            json.loads(get(API, path, headers=authenticated))
        json.loads(get(API, "/v1/dashboard/personal-risk", headers=authenticated,
                       lat=33.749, lon=-84.388))
        json.loads(get(API, "/v1/map/personal/forecast", headers=authenticated))
    finally:
        request = urllib.request.Request(supabase + "/auth/v1/logout?scope=local",
            headers={**headers, **authenticated}, method="POST")
        with urllib.request.urlopen(request, timeout=30):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-fresh", action="store_true")
    parser.add_argument("--auth", action="store_true", help="Use the dedicated test account in backend/.env")
    args = parser.parse_args()
    for path in ("/", "/dashboard", "/map", "/symptoms"):
        assert b"<html" in get(WEB, path).lower(), path
    get(API, "/health")
    try:
        get(API, "/v1/symptoms")
    except urllib.error.HTTPError as error:
        assert error.code == 401, error.code
    else:
        raise AssertionError("Symptom history accepted an anonymous request")
    dashboard = json.loads(get(API, "/v1/dashboard/risk", lat=33.749, lon=-84.388))
    metadata = json.loads(get(API, "/v1/map/forecast"))
    assert dashboard["source_run"] == metadata["source_run"]
    assert len(dashboard["days"]) == 4
    if args.require_fresh:
        assert not dashboard["stale"] and not metadata["stale"]
    hourly = {hour["valid_at"]: hour for hour in dashboard["hourly"]}
    frame = next(frame for frame in metadata["frames"]
                 if frame["available"] and frame["valid_at"] in hourly)
    params = {"run_id": metadata["run_id"], "index": frame["index"]}
    cell = json.loads(get(API, "/v1/map/cell", **params, lat=33.749, lon=-84.388))
    assert cell["risk_score"] == hourly[frame["valid_at"]]["general_score"]
    image = json.loads(get(API, "/v1/map/frame", **params))
    assert image["image"].startswith("data:image/png;base64,")
    trends = json.loads(get(API, "/v1/dashboard/environmental-trends", lat=33.749, lon=-84.388))
    assert trends
    if args.auth:
        check_authenticated_reads()
    print(json.dumps({"web_routes": "pass", "private_auth": "pass",
        "source_run": metadata["source_run"], "stale": dashboard["stale"],
        "daily_scores": [day["score"] for day in dashboard["days"]],
        "frames": len(metadata["frames"]), "dashboard_map_agree": True,
        "map_png": "pass", "environmental_trends": "pass",
        "authenticated_reads": "pass" if args.auth else "not requested"}, indent=2))


if __name__ == "__main__":
    main()
