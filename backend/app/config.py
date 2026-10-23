"""API settings. Infrastructure clients must be initialized only when used."""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    dashboard_demo_enabled: bool = False
    cors_origins: tuple[str, ...] = ("http://localhost:8081", "http://127.0.0.1:8081")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            dashboard_demo_enabled=os.getenv("DASHBOARD_DEMO_ENABLED", "false").lower() == "true",
            cors_origins=tuple(
                origin.strip()
                for origin in os.getenv(
                    "CORS_ORIGINS", "http://localhost:8081,http://127.0.0.1:8081"
                ).split(",")
                if origin.strip()
            ),
        )
