"""API settings. Infrastructure clients must be initialized only when used."""

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    supabase_url: str = ""
    dashboard_demo_enabled: bool = False
    forecast_provider: str = "disabled"
    forecast_bucket: str = ""
    live_forecast_store: str = "disabled"
    live_forecast_path: str = "data/live-forecast.json"
    cloud_project: str = ""
    forecast_raw_bucket: str = ""
    forecast_processed_bucket: str = ""
    forecast_delivery_bucket: str = ""
    forecast_catalog_directory: str = ""
    forecast_delivery_base_url: str = ""
    forecast_cdn_key_name: str = ""
    forecast_cdn_signing_key: str = field(default="", repr=False)
    tasks_enabled: bool = False
    tasks_location: str = ""
    tasks_queue: str = ""
    personalization_worker_url: str = ""
    tasks_service_account_email: str = ""
    worker_audience: str = ""
    worker_service_account_email: str = ""
    places_enabled: bool = False
    places_api_key: str = field(default="", repr=False)
    places_requests_per_minute: int = 5
    cors_origins: tuple[str, ...] = ("http://localhost:8081", "http://127.0.0.1:8081")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            cloud_project=os.getenv("CLOUD_PROJECT", ""),
            forecast_raw_bucket=os.getenv("FORECAST_RAW_BUCKET", ""),
            forecast_processed_bucket=os.getenv("FORECAST_PROCESSED_BUCKET", ""),
            forecast_delivery_bucket=os.getenv("FORECAST_DELIVERY_BUCKET", ""),
            forecast_catalog_directory=os.getenv("FORECAST_CATALOG_DIRECTORY", ""),
            forecast_delivery_base_url=os.getenv("FORECAST_DELIVERY_BASE_URL", ""),
            forecast_cdn_key_name=os.getenv("FORECAST_CDN_KEY_NAME", ""),
            forecast_cdn_signing_key=os.getenv("FORECAST_CDN_SIGNING_KEY", ""),
            tasks_enabled=os.getenv("TASKS_ENABLED", "false").lower() == "true",
            tasks_location=os.getenv("TASKS_LOCATION", ""),
            tasks_queue=os.getenv("TASKS_QUEUE", ""),
            personalization_worker_url=os.getenv("PERSONALIZATION_WORKER_URL", ""),
            tasks_service_account_email=os.getenv("TASKS_SERVICE_ACCOUNT_EMAIL", ""),
            worker_audience=os.getenv("WORKER_AUDIENCE", ""),
            worker_service_account_email=os.getenv("WORKER_SERVICE_ACCOUNT_EMAIL", ""),
            places_enabled=os.getenv("PLACES_ENABLED", "false").lower() == "true",
            places_api_key=os.getenv("PLACES_API_KEY", ""),
            places_requests_per_minute=int(os.getenv("PLACES_REQUESTS_PER_MINUTE", "5")),
            live_forecast_store=os.getenv("LIVE_FORECAST_STORE", "disabled"),
            live_forecast_path=os.getenv("LIVE_FORECAST_PATH", "data/live-forecast.json"),
            supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
            forecast_provider=os.getenv("FORECAST_PROVIDER", "disabled"),
            forecast_bucket=os.getenv("FORECAST_BUCKET", ""),
            dashboard_demo_enabled=os.getenv("DASHBOARD_DEMO_ENABLED", "false").lower() == "true",
            cors_origins=tuple(
                origin.strip()
                for origin in os.getenv(
                    "CORS_ORIGINS", "http://localhost:8081,http://127.0.0.1:8081"
                ).split(",")
                if origin.strip()
            ),
        )
