"""Offline configuration invariants; no Terraform/cloud/Docker invocation."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads(Path(__file__).with_name("main.tf.json").read_text())


class InfrastructureTests(unittest.TestCase):
    def test_dangerous_features_default_off(self):
        for name in ("enable_cdn", "enable_places", "enable_tasks", "api_allow_unauthenticated", "create_scheduler"):
            self.assertIs(CONFIG["variable"][name]["default"], False)

    def test_schedule_never_enabled_and_not_created_by_default(self):
        schedule = CONFIG["resource"]["google_cloud_scheduler_job"]["forecast"]
        self.assertIs(schedule["paused"], True)
        self.assertEqual(schedule["count"], "${var.create_scheduler ? 1 : 0}")
        self.assertEqual(schedule["retry_config"]["retry_count"], 0)
        self.assertIn("oauth_token", schedule["http_target"])

    def test_job_limits(self):
        job = CONFIG["resource"]["google_cloud_run_v2_job"]["forecast"]["template"]
        self.assertEqual((job["task_count"], job["parallelism"]), (1, 1))
        task = job["template"]
        self.assertEqual((task["timeout"], task["max_retries"]), ("300s", 0))
        self.assertEqual(task["containers"]["resources"]["limits"], {"cpu": "1", "memory": "512Mi"})
        self.assertNotIn("--execute-now", str(job))

    def test_private_storage_and_no_project_grants(self):
        bucket = CONFIG["resource"]["google_storage_bucket"]["forecast"]
        self.assertEqual(bucket["public_access_prevention"], "enforced")
        self.assertIs(bucket["force_destroy"], False)
        self.assertIs(bucket["lifecycle"]["prevent_destroy"], True)
        self.assertNotIn("google_project_iam_member", CONFIG["resource"])
        self.assertNotIn("google_project_service", CONFIG["resource"])
        for binding in CONFIG["resource"]["google_storage_bucket_iam_member"].values():
            self.assertNotEqual(binding["member"], "allUsers")
            if binding["role"] == "roles/storage.objectAdmin":
                self.assertIn("condition", binding)

    def test_services_scale_to_zero_worker_private(self):
        for service in CONFIG["resource"]["google_cloud_run_v2_service"].values():
            self.assertEqual(service["template"]["scaling"], {"min_instance_count": 0, "max_instance_count": 1})
        members = CONFIG["resource"]["google_cloud_run_v2_service_iam_member"]
        self.assertNotEqual(members["worker_dispatch"]["member"], "allUsers")

    def test_containers_nonroot_and_no_model_context(self):
        for name in ("api", "worker", "forecast"):
            docker = (ROOT / "backend" / f"Dockerfile.{name}").read_text()
            self.assertIn("USER 10001:10001", docker)
            self.assertNotIn("COPY .", docker)
            self.assertIn("forecast_job/__init__.py forecast_job/storage.py /srv/forecast_job/", docker)
        ignore = (ROOT / "backend/.dockerignore").read_text()
        self.assertIn("forecast_pipeline/reference/**", ignore)
        self.assertNotIn("!ml-work", ignore)
        self.assertIn("app.cloud:app", (ROOT / "backend/Dockerfile.api").read_text())

    def test_delivery_bucket_and_pointer_writer_are_configured(self):
        env = CONFIG["locals"]["api_env"]
        self.assertIn("FORECAST_DELIVERY_BUCKET", env)
        binding = CONFIG["resource"]["google_storage_bucket_iam_member"]["processor_delivery_pointer"]
        self.assertIn("processed/current.json", binding["condition"]["expression"])
        self.assertIn('forecast["delivery"]', binding["bucket"])

    def test_cdn_restricts_origin_and_keys_and_is_https(self):
        resources = CONFIG["resource"]
        self.assertEqual(resources["google_compute_global_forwarding_rule"]["cdn"]["port_range"], "443")
        self.assertEqual(resources["google_compute_backend_bucket"]["forecast"]["cdn_policy"]["signed_url_cache_max_age_sec"], 300)
        self.assertIs(CONFIG["variable"]["cdn_signing_key"]["sensitive"], True)
        self.assertIn("depends_on", resources["google_storage_bucket_iam_member"]["cdn_read"])


if __name__ == "__main__":
    unittest.main()
