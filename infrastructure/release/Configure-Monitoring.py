"""Configure forecast failure and missing-freshness monitoring using the operator login."""
import json
import shutil
import subprocess
import urllib.error
import urllib.request

PROJECT = "asthma-aware"
ACCOUNT = "asthmaaware26@gmail.com"
CHANNEL = f"projects/{PROJECT}/notificationChannels/8593980526254201537"


def main():
    token = subprocess.run(
        [shutil.which("gcloud"), "auth", "print-access-token", f"--account={ACCOUNT}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    def api(service, method, path, body=None):
        request = urllib.request.Request(
            f"https://{service}.googleapis.com/{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            details = json.load(error)
            raise RuntimeError(details.get("error", {}).get("message", str(error))) from None

    metrics = {
        "asthma_integration_job_failure": {
            "description": "Failed integration forecast, availability check or freshness executions",
            "filter": 'resource.type="cloud_run_job" AND '
                      'resource.labels.job_name=("asthmaaware-forecast" OR "asthmaaware-forecast-check" OR "asthmaaware-freshness") '
                      'AND severity>=ERROR',
        },
        "asthma_integration_freshness_success": {
            "description": "Successful checks of usable, recently issued published forecast objects",
            "filter": 'resource.type="cloud_run_job" AND '
                      'resource.labels.job_name="asthmaaware-freshness" AND '
                      'textPayload:"freshness_probe status=success"',
        },
    }
    for name, body in metrics.items():
        body = {"name": name, **body}
        api("logging", "PUT", f"v2/projects/{PROJECT}/metrics/{name}", body)
        print(f"Configured metric: {name}")

    policy = {
        "displayName": "AsthmaAware integration freshness checks missing",
        "enabled": True,
        "combiner": "OR",
        "notificationChannels": [CHANNEL],
        "conditions": [{
            "displayName": "No successful freshness probe for 2.5 hours",
            "conditionAbsent": {
                "filter": 'resource.type="cloud_run_job" AND '
                          'metric.type="logging.googleapis.com/user/asthma_integration_freshness_success"',
                "duration": "9000s",
                "aggregations": [{"alignmentPeriod": "600s", "perSeriesAligner": "ALIGN_SUM"}],
                "trigger": {"count": 1},
            },
        }],
        "documentation": {"mimeType": "text/markdown", "content":
            "Check asthmaaware-freshness Scheduler delivery and Cloud Run executions. "
            "A successful probe requires a forecast issued within 40 hours, remaining "
            "prediction times, and existing risk and map objects. Inspect the publisher "
            "before changing published/current.json; keep the previous complete publication."},
    }
    existing = api("monitoring", "GET", f"v3/projects/{PROJECT}/alertPolicies")
    match = next((p for p in existing.get("alertPolicies", [])
                  if p["displayName"] == policy["displayName"]), None)
    if match:
        policy["name"] = match["name"]
        api("monitoring", "PATCH", f'v3/{match["name"]}', policy)
    else:
        api("monitoring", "POST", f"v3/projects/{PROJECT}/alertPolicies", policy)
    print("Configured freshness absence policy")


if __name__ == "__main__":
    main()
